#![allow(dead_code)]

use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};

pub(crate) const MAX_NATIVE_NOTIFICATIONS_PER_EVALUATION: usize = 3;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub(crate) struct DeadlineNotificationEvent {
    pub(crate) stable_id: String,
    pub(crate) revision: String,
    pub(crate) course_code: String,
    pub(crate) due_at: String,
    pub(crate) submitted: bool,
    pub(crate) threshold: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct DeadlineNotification {
    pub(crate) title: String,
    pub(crate) body: String,
    pub(crate) ledger_key: String,
    pub(crate) delivery_keys: Vec<String>,
}

pub(crate) trait NotificationSink: Send + Sync {
    fn send(&self, title: &str, body: &str) -> Result<(), String>;
}

pub(crate) struct RecordingNotificationSink {
    sent: Mutex<Vec<(String, String)>>,
}

impl RecordingNotificationSink {
    pub(crate) fn new() -> Self {
        Self {
            sent: Mutex::new(Vec::new()),
        }
    }

    pub(crate) fn sent(&self) -> Vec<(String, String)> {
        self.sent.lock().expect("sink lock").clone()
    }
}

impl NotificationSink for RecordingNotificationSink {
    fn send(&self, title: &str, body: &str) -> Result<(), String> {
        self.sent
            .lock()
            .expect("sink lock")
            .push((title.to_string(), body.to_string()));
        Ok(())
    }
}

pub(crate) struct FailingNotificationSink;

impl NotificationSink for FailingNotificationSink {
    fn send(&self, _title: &str, _body: &str) -> Result<(), String> {
        Err("os_notification_failed".to_string())
    }
}

pub(crate) struct OsNotificationSink {
    app: tauri::AppHandle,
}

impl OsNotificationSink {
    pub(crate) fn new(app: tauri::AppHandle) -> Self {
        Self { app }
    }
}

impl NotificationSink for OsNotificationSink {
    fn send(&self, title: &str, body: &str) -> Result<(), String> {
        use tauri_plugin_notification::NotificationExt;
        self.app
            .notification()
            .builder()
            .title(title)
            .body(body)
            .show()
            .map_err(|_| "os_notification_failed".to_string())
    }
}

pub(crate) struct DeadlineNotificationPolicy {
    enabled: bool,
    delivered: HashSet<String>,
}

impl DeadlineNotificationPolicy {
    pub(crate) fn new(enabled: bool, delivered: HashSet<String>) -> Self {
        Self { enabled, delivered }
    }

    pub(crate) fn evaluate(
        &self,
        events: &[DeadlineNotificationEvent],
    ) -> Vec<DeadlineNotification> {
        if !self.enabled {
            return Vec::new();
        }
        let mut due: Vec<DeadlineNotification> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        for event in events {
            if event.submitted || event.due_at.is_empty() || event.stable_id.is_empty() {
                continue;
            }
            if event.threshold != "DUE_TODAY" && event.threshold != "OVERDUE" {
                continue;
            }
            let key = format!("{}|{}|{}", event.stable_id, event.revision, event.threshold);
            if self.delivered.contains(&key) {
                continue;
            }
            if !seen.insert(key.clone()) {
                continue;
            }
            let body = format!("{} assignment due at {}", event.course_code, event.due_at);
            due.push(DeadlineNotification {
                title: "StudyHub".to_string(),
                body,
                delivery_keys: vec![key.clone()],
                ledger_key: key,
            });
        }
        due.sort_by(|a, b| a.body.cmp(&b.body));
        if due.len() <= MAX_NATIVE_NOTIFICATIONS_PER_EVALUATION {
            return due;
        }
        let remaining = due.len();
        let summary = DeadlineNotification {
            title: "StudyHub".to_string(),
            body: format!("You have {remaining} assignments due or overdue."),
            ledger_key: format!("summary|{}", due.len()),
            delivery_keys: due.iter().map(|item| item.ledger_key.clone()).collect(),
        };
        vec![summary]
    }
}

pub(crate) fn load_delivery_ledger(path: &PathBuf) -> HashSet<String> {
    let Ok(raw) = std::fs::read_to_string(path) else {
        return HashSet::new();
    };
    raw.lines().map(str::to_string).collect()
}

pub(crate) fn append_delivery_ledger(path: &PathBuf, keys: &[String]) {
    let existing = load_delivery_ledger(path);
    let mut merged: Vec<String> = existing.into_iter().collect();
    for key in keys {
        if !merged.iter().any(|value| value == key) {
            merged.push(key.clone());
        }
    }
    merged.sort();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(path, merged.join("\n") + "\n");
}

pub(crate) fn load_notification_preference(path: &PathBuf) -> bool {
    let Ok(raw) = fs::read_to_string(path) else {
        return false;
    };
    matches!(raw.trim(), "1")
}

pub(crate) fn save_notification_preference(path: &PathBuf, enabled: bool) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|_| "notification_preference_write_failed".to_string())?;
    }
    let content = if enabled { "1\n" } else { "0\n" };
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, content).map_err(|_| "notification_preference_write_failed".to_string())?;
    fs::rename(&temporary, path).map_err(|_| {
        let _ = fs::remove_file(&temporary);
        "notification_preference_write_failed".to_string()
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event(id: &str, revision: &str, submitted: bool) -> DeadlineNotificationEvent {
        DeadlineNotificationEvent {
            stable_id: id.to_string(),
            revision: revision.to_string(),
            course_code: "TEST1001".to_string(),
            due_at: "17:00".to_string(),
            submitted,
            threshold: "OVERDUE".to_string(),
        }
    }

    #[test]
    fn disabled_policy_sends_nothing() {
        let policy = DeadlineNotificationPolicy::new(false, HashSet::new());
        assert!(policy.evaluate(&[event("a", "r1", false)]).is_empty());
    }

    #[test]
    fn submitted_and_duplicate_events_are_suppressed() {
        let policy = DeadlineNotificationPolicy::new(true, HashSet::new());
        let result = policy.evaluate(&[
            event("a", "r1", false),
            event("a", "r1", false),
            event("b", "r1", true),
        ]);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].ledger_key, "a|r1|OVERDUE");
    }

    #[test]
    fn deadline_change_makes_new_revision_eligible() {
        let mut delivered = HashSet::new();
        delivered.insert("a|r1|OVERDUE".to_string());
        let policy = DeadlineNotificationPolicy::new(true, delivered);
        let result = policy.evaluate(&[event("a", "r2", false)]);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].ledger_key, "a|r2|OVERDUE");
    }

    #[test]
    fn burst_limit_emits_summary() {
        let policy = DeadlineNotificationPolicy::new(true, HashSet::new());
        let events: Vec<DeadlineNotificationEvent> = (0..8)
            .map(|index| event(&format!("a{index}"), "r1", false))
            .collect();
        let result = policy.evaluate(&events);
        assert_eq!(result.len(), 1);
        assert!(result[0].body.contains("8 assignments"));
    }

    #[test]
    fn recording_sink_captures_and_failing_sink_errors() {
        let recording = RecordingNotificationSink::new();
        recording.send("StudyHub", "due").expect("record");
        assert_eq!(recording.sent().len(), 1);
        assert!(FailingNotificationSink.send("StudyHub", "due").is_err());
    }

    #[test]
    fn notification_preference_persists_atomically() {
        let root = std::env::temp_dir().join(format!("studyhub-notif-pref-{}", std::process::id()));
        let path = root.join("preference");
        let _ = std::fs::remove_dir_all(&root);
        assert_eq!(load_notification_preference(&path), false);
        save_notification_preference(&path, true).expect("save true");
        assert_eq!(load_notification_preference(&path), true);
        save_notification_preference(&path, false).expect("save false");
        assert_eq!(load_notification_preference(&path), false);
        std::fs::write(&path, "garbage").expect("write garbage");
        assert_eq!(load_notification_preference(&path), false);
        assert!(!path.with_extension("tmp").exists());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn burst_summary_commits_all_keys_and_suppresses_next_evaluation() {
        let policy = DeadlineNotificationPolicy::new(true, HashSet::new());
        let events: Vec<DeadlineNotificationEvent> = (0..8)
            .map(|index| event(&format!("a{index}"), "r1", false))
            .collect();
        let first = policy.evaluate(&events);
        assert_eq!(first.len(), 1);
        assert_eq!(first[0].delivery_keys.len(), 8);

        let mut delivered: HashSet<String> = HashSet::new();
        for key in &first[0].delivery_keys {
            delivered.insert(key.clone());
        }
        let second = DeadlineNotificationPolicy::new(true, delivered).evaluate(&events);
        assert!(second.is_empty());
    }

    #[test]
    fn threshold_kind_distinguishes_due_today_from_overdue() {
        let policy = DeadlineNotificationPolicy::new(true, HashSet::new());
        let mut due_today = event("a", "r1", false);
        due_today.threshold = "DUE_TODAY".to_string();
        let mut overdue = event("a", "r1", false);
        overdue.threshold = "OVERDUE".to_string();
        let result = policy.evaluate(&[due_today, overdue]);
        let keys: Vec<&str> = result.iter().map(|item| item.ledger_key.as_str()).collect();
        assert!(keys.contains(&"a|r1|DUE_TODAY"));
        assert!(keys.contains(&"a|r1|OVERDUE"));
    }
}
