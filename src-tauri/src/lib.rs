use std::env;
use std::ffi::OsString;
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU16, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

#[cfg(not(debug_assertions))]
use tauri::path::BaseDirectory;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;

mod canvas_connection;
mod canvas_oauth;
mod credential_handoff;
mod credential_store;
mod credential_transport;
mod notifications;

use crate::canvas_connection::{
    configure_canvas_development_connection_in_store,
};
use crate::canvas_oauth::{
    CanvasConnectionState, CanvasCredentialManager, CanvasOAuthFailure, CanvasOAuthStart,
};
use crate::credential_handoff::{
    CanvasDefaultResolver, CanvasScopeSetResolver, HandoffError, HandoffFailure,
};
use crate::credential_store::credential_store_for_profile;
use crate::notifications::{
    dispatch_deadline_notifications, load_delivery_ledger, load_notification_preference,
    save_notification_preference, DeadlineNotificationEvent, DeadlineNotificationPolicy,
    OsNotificationSink,
};

const STARTUP_TIMEOUT: Duration = Duration::from_secs(30);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(4);
const FALLBACK_URL: &str = "tauri://localhost/index.html";
const PRODUCTION_IDENTIFIER: &str = "io.studyhublocal.desktop";
const DEVELOPMENT_IDENTIFIER: &str = "io.studyhublocal.desktop.dev";
const DEMO_IDENTIFIER: &str = "io.studyhublocal.desktop.demo";
const MAX_BACKUP_ARCHIVE_BYTES: u64 = 20 * 1024 * 1024;
static RESTORE_ID_COUNTER: AtomicU64 = AtomicU64::new(0);
const RESTORE_RELINK_CAPABILITY_VERSION: u8 = 1;
const SUPPORTED_ACADEMIC_EXTENSIONS: &[&str] = &[
    "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "csv", "tsv", "txt", "md", "py", "ipynb",
    "r", "html", "htm", "xhtml", "xml", "svg", "png", "jpg", "jpeg", "webp",
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RuntimeProfile {
    Production,
    Development,
    DemoTest,
}

impl RuntimeProfile {
    fn environment_name(self) -> &'static str {
        match self {
            Self::Production => "production",
            Self::Development => "development",
            Self::DemoTest => "demo-test",
        }
    }

    fn display_name(self) -> &'static str {
        match self {
            Self::Production => "StudyHub Local",
            Self::Development => "StudyHub Dev",
            Self::DemoTest => "StudyHub Demo",
        }
    }

    fn allows_external_sources(self) -> bool {
        self != Self::DemoTest
    }
}

fn resolve_runtime_profile(
    identifier: &str,
    debug_build: bool,
    has_test_root: bool,
) -> Result<RuntimeProfile, String> {
    if has_test_root {
        return Ok(RuntimeProfile::DemoTest);
    }
    match (debug_build, identifier) {
        (false, PRODUCTION_IDENTIFIER) => Ok(RuntimeProfile::Production),
        (true, DEVELOPMENT_IDENTIFIER) => Ok(RuntimeProfile::Development),
        (true, DEMO_IDENTIFIER) => Ok(RuntimeProfile::DemoTest),
        (true, PRODUCTION_IDENTIFIER) => Err("development_profile_required".to_string()),
        _ => Err("invalid_runtime_profile".to_string()),
    }
}

#[derive(Clone)]
struct BackendConfig {
    executable: PathBuf,
    prefix_args: Vec<OsString>,
    packaged: bool,
    static_dir: PathBuf,
    katex_dir: PathBuf,
    mac_ocr_path: PathBuf,
    markitdown_helper_path: PathBuf,
    profile: RuntimeProfile,
    runtime_dir: PathBuf,
    data_dir: PathBuf,
    cache_dir: PathBuf,
    log_dir: PathBuf,
    database_path: PathBuf,
    config_path: PathBuf,
    webview_data_dir: Option<PathBuf>,
}

struct BackendProcess {
    child: Child,
    url: String,
}

struct BackendState {
    config: BackendConfig,
    process: Mutex<Option<BackendProcess>>,
    allowed_port: Arc<AtomicU16>,
    last_error: Mutex<String>,
    canvas_manager: Arc<CanvasCredentialManager<crate::credential_store::CredentialStoreBackend>>,
    notification_enabled: Mutex<bool>,
}

#[cfg(debug_assertions)]
fn development_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri must live inside the project")
        .to_path_buf()
}

fn backend_config(app: &tauri::App) -> Result<BackendConfig, String> {
    let test_root = env::var_os("STUDYHUB_DESKTOP_TEST_ROOT").map(PathBuf::from);
    let profile = resolve_runtime_profile(
        app.config().identifier.as_str(),
        cfg!(debug_assertions),
        test_root.is_some(),
    )?;
    let (app_data, app_config) = if let Some(root) = test_root {
        (root.join("data"), root.join("config"))
    } else {
        (
            app.path()
                .app_data_dir()
                .map_err(|error| error.to_string())?,
            app.path()
                .app_config_dir()
                .map_err(|error| error.to_string())?,
        )
    };

    #[cfg(debug_assertions)]
    let (
        executable,
        prefix_args,
        packaged,
        static_dir,
        katex_dir,
        mac_ocr_path,
        markitdown_helper_path,
    ) = {
        let root = development_root();
        (
            PathBuf::from(
                env::var("STUDYHUB_DESKTOP_PYTHON").unwrap_or_else(|_| "python3".to_string()),
            ),
            vec![root.join("server.py").into_os_string()],
            false,
            root.join("static"),
            root.join("node_modules/katex/dist"),
            root.join("node_modules/mac-ocr/bin/mac-ocr"),
            root.join("src-tauri/backend/studyhub-markitdown"),
        )
    };

    #[cfg(not(debug_assertions))]
    let (
        executable,
        prefix_args,
        packaged,
        static_dir,
        katex_dir,
        mac_ocr_path,
        markitdown_helper_path,
    ) = (
        app.path()
            .resolve("backend/studyhub-backend", BaseDirectory::Resource)
            .map_err(|error| error.to_string())?,
        Vec::new(),
        true,
        app.path()
            .resolve("static", BaseDirectory::Resource)
            .map_err(|error| error.to_string())?,
        app.path()
            .resolve("katex", BaseDirectory::Resource)
            .map_err(|error| error.to_string())?,
        app.path()
            .resolve("reader-tools/mac-ocr", BaseDirectory::Resource)
            .map_err(|error| error.to_string())?,
        app.path()
            .resolve("reader-tools/studyhub-markitdown", BaseDirectory::Resource)
            .map_err(|error| error.to_string())?,
    );

    Ok(BackendConfig {
        executable,
        prefix_args,
        packaged,
        static_dir,
        katex_dir,
        mac_ocr_path,
        markitdown_helper_path,
        profile,
        runtime_dir: app_data.clone(),
        data_dir: app_data.join("data"),
        cache_dir: app_data.join("cache"),
        log_dir: app_data.join("logs"),
        database_path: app_data.join("data/studyhub.sqlite"),
        config_path: app_config.join("settings.env"),
        webview_data_dir: (profile != RuntimeProfile::Production).then(|| app_data.join("webview")),
    })
}

fn parse_port(url: &str) -> Result<u16, String> {
    url.rsplit_once(':')
        .and_then(|(_, port)| port.parse::<u16>().ok())
        .ok_or_else(|| "invalid_backend_url".to_string())
}

fn health_check(url: &str) -> bool {
    let Ok(port) = parse_port(url) else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}")
            .parse()
            .expect("valid loopback socket"),
        Duration::from_millis(500),
    ) else {
        return false;
    };
    let request =
        format!("GET /api/health HTTP/1.1\r\nHost: localhost:{port}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok() && response.starts_with("HTTP/1.0 200")
}

fn start_backend(
    config: &BackendConfig,
    canvas_manager: &Arc<CanvasCredentialManager<crate::credential_store::CredentialStoreBackend>>,
) -> Result<BackendProcess, String> {
    std::fs::create_dir_all(&config.runtime_dir).map_err(|_| "runtime_unavailable".to_string())?;
    for directory in [&config.data_dir, &config.cache_dir, &config.log_dir] {
        std::fs::create_dir_all(directory).map_err(|_| "runtime_unavailable".to_string())?;
    }
    if let Some(parent) = config.config_path.parent() {
        std::fs::create_dir_all(parent).map_err(|_| "config_unavailable".to_string())?;
    }
    if config.packaged && !config.executable.is_file() {
        return Err("backend_missing".to_string());
    }

    let mut command = Command::new(&config.executable);
    for inherited in [
        "DATABASE_PATH",
        "DEMO_MODE",
        "OPENAI_API_BASE",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_VECTOR_STORE_ID",
        credential_transport::TRANSPORT_FD_ENV,
        credential_transport::TRANSPORT_KIND_ENV,
        credential_transport::TRANSPORT_PROTOCOL_ENV,
        "STUDYHUB_CACHE_DIR",
        "STUDYHUB_CONFIG_PATH",
        "STUDYHUB_DATA_DIR",
        "STUDYHUB_DESKTOP_TEST_ROOT",
        "STUDYHUB_LOG_DIR",
        "STUDYHUB_MAC_OCR",
        "STUDYHUB_MARKITDOWN_HELPER",
        "STUDYHUB_RUNTIME_DIR",
        "STUDYHUB_RUNTIME_PROFILE",
        "STUDY_LIBRARY_PATH",
    ] {
        command.env_remove(inherited);
    }
    command
        .args(&config.prefix_args)
        .args(["serve", "--port", "0"])
        .env("HOST", "127.0.0.1")
        .env("STUDYHUB_DESKTOP", "true")
        .env(
            "STUDYHUB_RUNTIME_PROFILE",
            config.profile.environment_name(),
        )
        .env("STUDYHUB_RUNTIME_DIR", &config.runtime_dir)
        .env("STUDYHUB_DATA_DIR", &config.data_dir)
        .env("STUDYHUB_CACHE_DIR", &config.cache_dir)
        .env("STUDYHUB_LOG_DIR", &config.log_dir)
        .env("DATABASE_PATH", &config.database_path)
        .env("STUDYHUB_CONFIG_PATH", &config.config_path)
        .env("STUDYHUB_STATIC_DIR", &config.static_dir)
        .env("STUDYHUB_KATEX_DIR", &config.katex_dir)
        .env("STUDYHUB_MAC_OCR", &config.mac_ocr_path)
        .env("STUDYHUB_MARKITDOWN_HELPER", &config.markitdown_helper_path)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    #[cfg(unix)]
    let credential_transport = credential_transport::configure_child_transport(&mut command).ok();

    let mut child = command.spawn().map_err(|error| match error.kind() {
        std::io::ErrorKind::NotFound => "backend_missing".to_string(),
        std::io::ErrorKind::PermissionDenied => "backend_permission".to_string(),
        _ => "backend_spawn_failed".to_string(),
    })?;
    #[cfg(unix)]
    if let Some(transport) = credential_transport {
        let resolver: CanvasDefaultResolver = {
            let manager = Arc::clone(canvas_manager);
            Box::new(move || {
                manager
                    .resolve_canvas_default()
                    .map_err(|error| HandoffError::new(oauth_failure_to_handoff(error.failure())))
            })
        };
        let scope_set_resolver: CanvasScopeSetResolver = {
            let manager = Arc::clone(canvas_manager);
            Box::new(move || {
                manager
                    .resolve_canvas_scope_set()
                    .map_err(|error| HandoffError::new(oauth_failure_to_handoff(error.failure())))
            })
        };
        credential_transport::spawn_backend_credential_transport(
            transport,
            child.id(),
            config.profile,
            Some(resolver),
            Some(scope_set_resolver),
        );
    }
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "backend_output_unavailable".to_string())?;
    if let Some(stderr) = child.stderr.take() {
        thread::spawn(move || for _ in BufReader::new(stderr).lines() {});
    }
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(url) = line.strip_prefix("StudyHub Local running at ") {
                let _ = sender.send(url.trim().to_string());
                break;
            }
        }
    });

    let startup_deadline = Instant::now() + STARTUP_TIMEOUT;
    let url = loop {
        if Instant::now() >= startup_deadline {
            stop_child(&mut child);
            return Err("backend_start_timeout".to_string());
        }
        match receiver.recv_timeout(Duration::from_millis(200)) {
            Ok(url) => break url,
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                stop_child(&mut child);
                return Err("backend_exited_before_health".to_string());
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
        if child
            .try_wait()
            .map_err(|_| "backend_status_failed".to_string())?
            .is_some()
        {
            return Err("backend_exited_before_health".to_string());
        }
    };

    let health_deadline = Instant::now() + STARTUP_TIMEOUT;
    while Instant::now() < health_deadline {
        if health_check(&url) {
            return Ok(BackendProcess { child, url });
        }
        if child
            .try_wait()
            .map_err(|_| "backend_status_failed".to_string())?
            .is_some()
        {
            return Err("backend_exited_before_health".to_string());
        }
        thread::sleep(Duration::from_millis(150));
    }

    stop_child(&mut child);
    Err("backend_health_timeout".to_string())
}

fn stop_child(child: &mut Child) {
    if child.try_wait().ok().flatten().is_some() {
        return;
    }
    #[cfg(unix)]
    unsafe {
        libc::kill(child.id() as i32, libc::SIGTERM);
    }
    #[cfg(not(unix))]
    let _ = child.kill();

    let deadline = Instant::now() + SHUTDOWN_TIMEOUT;
    while Instant::now() < deadline {
        if child.try_wait().ok().flatten().is_some() {
            return;
        }
        thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn safe_error_message(code: &str) -> &'static str {
    match code {
        "backend_missing" => "The packaged local service could not be found.",
        "backend_permission" => "macOS did not allow the packaged local service to start.",
        "runtime_unavailable" | "config_unavailable" => {
            "StudyHub could not prepare its local application data folder."
        }
        "backend_start_timeout" | "backend_health_timeout" => {
            "The local service did not become ready in time."
        }
        _ => "The local service stopped before StudyHub was ready.",
    }
}

fn oauth_failure_to_handoff(failure: CanvasOAuthFailure) -> HandoffFailure {
    match failure {
        CanvasOAuthFailure::NotProvisioned => HandoffFailure::MissingCredential,
        CanvasOAuthFailure::ReconnectRequired => HandoffFailure::MissingCredential,
        CanvasOAuthFailure::Missing => HandoffFailure::MissingCredential,
        CanvasOAuthFailure::BackendUnavailable => HandoffFailure::BackendUnavailable,
        CanvasOAuthFailure::AccessDeniedStore => HandoffFailure::AccessDenied,
        CanvasOAuthFailure::OperationFailed => HandoffFailure::OperationFailed,
        _ => HandoffFailure::MissingCredential,
    }
}

fn navigate_to_fallback(app: &tauri::AppHandle) {
    if let (Some(window), Ok(url)) = (
        app.get_webview_window("main"),
        tauri::Url::parse(FALLBACK_URL),
    ) {
        let _ = window.navigate(url);
    }
}

fn start_backend_monitor(app: tauri::AppHandle) {
    thread::spawn(move || loop {
        thread::sleep(Duration::from_secs(1));
        let Some(state) = app.try_state::<BackendState>() else {
            break;
        };
        let stopped = {
            let Ok(mut process) = state.process.lock() else {
                break;
            };
            if let Some(active) = process.as_mut() {
                if active.child.try_wait().ok().flatten().is_some() {
                    *process = None;
                    true
                } else {
                    false
                }
            } else {
                false
            }
        };
        if stopped {
            state.allowed_port.store(0, Ordering::SeqCst);
            if let Ok(mut error) = state.last_error.lock() {
                *error = "backend_stopped".to_string();
            }
            navigate_to_fallback(&app);
        }
    });
}

#[tauri::command]
async fn choose_study_folder(
    app: tauri::AppHandle,
    state: tauri::State<'_, BackendState>,
) -> Result<Option<String>, String> {
    if !state.config.profile.allows_external_sources() {
        return Err("Demo/Test mode cannot select external study folders.".to_string());
    }
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .file()
            .blocking_pick_folder()
            .map(|path| path.to_string())
    })
    .await
    .map_err(|_| "StudyHub could not open the folder picker.".to_string())
}

#[tauri::command]
async fn choose_study_files(
    app: tauri::AppHandle,
    state: tauri::State<'_, BackendState>,
) -> Result<Option<Vec<String>>, String> {
    if !state.config.profile.allows_external_sources() {
        return Err("Demo/Test mode cannot select external study files.".to_string());
    }
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .file()
            .blocking_pick_files()
            .map(|paths| paths.into_iter().map(|path| path.to_string()).collect())
    })
    .await
    .map_err(|_| "StudyHub could not open the file picker.".to_string())
}

fn next_restore_id() -> String {
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let counter = RESTORE_ID_COUNTER.fetch_add(1, Ordering::SeqCst) as u128;
    let process = std::process::id() as u128;
    format!("{:032x}", timestamp ^ (process << 64) ^ counter)
}

fn stage_backup_archive(
    source: &Path,
    data_dir: &Path,
    profile: RuntimeProfile,
) -> Result<String, String> {
    if !profile.allows_external_sources() {
        return Err("Demo/Test mode cannot select external backup archives.".to_string());
    }
    let metadata = std::fs::symlink_metadata(source)
        .map_err(|_| "StudyHub could not read the selected backup archive.".to_string())?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("Select one regular backup ZIP file.".to_string());
    }
    if metadata.len() == 0 || metadata.len() > MAX_BACKUP_ARCHIVE_BYTES {
        return Err("The selected backup archive is empty or too large.".to_string());
    }

    let restore_dir = data_dir.join("restore-inbox");
    std::fs::create_dir_all(&restore_dir)
        .map_err(|_| "StudyHub could not prepare private restore staging.".to_string())?;
    let restore_id = next_restore_id();
    let temporary = restore_dir.join(format!(".{restore_id}.tmp"));
    let published = restore_dir.join(format!("{restore_id}.zip"));
    let result = (|| -> Result<(), String> {
        let mut input = File::open(source)
            .map_err(|_| "StudyHub could not read the selected backup archive.".to_string())?;
        let mut output = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
            .map_err(|_| "StudyHub could not stage the selected backup archive.".to_string())?;
        let mut copied = 0_u64;
        let mut buffer = [0_u8; 64 * 1024];
        loop {
            let count = input
                .read(&mut buffer)
                .map_err(|_| "StudyHub could not read the selected backup archive.".to_string())?;
            if count == 0 {
                break;
            }
            copied += count as u64;
            if copied > MAX_BACKUP_ARCHIVE_BYTES {
                return Err("The selected backup archive is too large.".to_string());
            }
            output
                .write_all(&buffer[..count])
                .map_err(|_| "StudyHub could not stage the selected backup archive.".to_string())?;
        }
        if copied != metadata.len() {
            return Err("The selected backup archive changed while it was copied.".to_string());
        }
        output
            .sync_all()
            .map_err(|_| "StudyHub could not finish private restore staging.".to_string())?;
        drop(output);
        std::fs::rename(&temporary, &published)
            .map_err(|_| "StudyHub could not publish private restore staging.".to_string())
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
        let _ = std::fs::remove_file(&published);
    }
    result.map(|()| restore_id)
}

#[tauri::command]
async fn choose_backup_archive(
    app: tauri::AppHandle,
    state: tauri::State<'_, BackendState>,
) -> Result<Option<String>, String> {
    if !state.config.profile.allows_external_sources() {
        return Err("Demo/Test mode cannot select external backup archives.".to_string());
    }
    let data_dir = state.config.data_dir.clone();
    let profile = state.config.profile;
    tauri::async_runtime::spawn_blocking(move || {
        let selected = app
            .dialog()
            .file()
            .add_filter("StudyHub backup", &["zip"])
            .blocking_pick_file();
        selected
            .map(|path| stage_backup_archive(Path::new(&path.to_string()), &data_dir, profile))
            .transpose()
    })
    .await
    .map_err(|_| "StudyHub could not open the backup picker.".to_string())?
}

fn json_string(value: &str) -> String {
    let mut escaped = String::with_capacity(value.len() + 2);
    escaped.push('"');
    for character in value.chars() {
        match character {
            '"' => escaped.push_str("\\\""),
            '\\' => escaped.push_str("\\\\"),
            '\n' => escaped.push_str("\\n"),
            '\r' => escaped.push_str("\\r"),
            '\t' => escaped.push_str("\\t"),
            value if value <= '\u{001f}' => {
                escaped.push_str(&format!("\\u{:04x}", value as u32));
            }
            value => escaped.push(value),
        }
    }
    escaped.push('"');
    escaped
}

fn valid_restore_context(restore_id: &str, source_stable_id: &str) -> bool {
    restore_id.len() == 32
        && restore_id
            .chars()
            .all(|value| value.is_ascii_hexdigit() && !value.is_ascii_uppercase())
        && !source_stable_id.is_empty()
        && source_stable_id.len() <= 200
        && source_stable_id.chars().enumerate().all(|(index, value)| {
            value.is_ascii_alphanumeric() || (index > 0 && matches!(value, '.' | '_' | ':' | '-'))
        })
}

fn next_relink_id() -> Result<String, String> {
    #[cfg(unix)]
    {
        let mut random = [0_u8; 16];
        File::open("/dev/urandom")
            .and_then(|mut source| source.read_exact(&mut random))
            .map_err(|_| "StudyHub secure random source is unavailable.".to_string())?;
        return Ok(random.iter().map(|value| format!("{value:02x}")).collect());
    }
    #[cfg(not(unix))]
    Err("StudyHub secure random source is unavailable.".to_string())
}

fn create_restore_relink_capability(
    source: &Path,
    data_dir: &Path,
    profile: RuntimeProfile,
    restore_id: &str,
    source_stable_id: &str,
) -> Result<(String, String, u64), String> {
    if !profile.allows_external_sources() {
        return Err("Demo/Test mode cannot select external restore sources.".to_string());
    }
    if !valid_restore_context(restore_id, source_stable_id) {
        return Err("Restore relink context is invalid.".to_string());
    }
    let metadata = std::fs::symlink_metadata(source)
        .map_err(|_| "StudyHub could not inspect the selected restore source.".to_string())?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("Choose one regular academic file.".to_string());
    }
    let extension = source
        .extension()
        .and_then(|value| value.to_str())
        .map(str::to_ascii_lowercase)
        .ok_or_else(|| "That file type is not supported.".to_string())?;
    if !SUPPORTED_ACADEMIC_EXTENSIONS.contains(&extension.as_str()) {
        return Err("That file type is not supported.".to_string());
    }
    let display_name = source
        .file_name()
        .and_then(|value| value.to_str())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "StudyHub could not read the selected file name.".to_string())?
        .to_string();
    let selected_path = source
        .to_str()
        .ok_or_else(|| "StudyHub could not read the selected restore source path.".to_string())?;
    if !source.is_absolute() {
        return Err("Choose an absolute restore source path.".to_string());
    }

    let inbox = data_dir.join("restore-relink-inbox");
    std::fs::create_dir_all(&inbox)
        .map_err(|_| "StudyHub could not prepare private relink staging.".to_string())?;
    #[cfg(unix)]
    std::fs::set_permissions(&inbox, std::fs::Permissions::from_mode(0o700))
        .map_err(|_| "StudyHub could not secure private relink staging.".to_string())?;
    let relink_id = next_relink_id()?;
    let descriptor = inbox.join(format!("{relink_id}.json"));
    let created_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let body = format!(
        "{{\"capability_version\":{},\"restore_id\":{},\"source_stable_id\":{},\"selected_path\":{},\"display_name\":{},\"created_at\":{}}}",
        RESTORE_RELINK_CAPABILITY_VERSION,
        json_string(restore_id),
        json_string(source_stable_id),
        json_string(selected_path),
        json_string(&display_name),
        created_at,
    );
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    options.mode(0o600);
    let mut output = options
        .open(&descriptor)
        .map_err(|_| "StudyHub could not create a private relink capability.".to_string())?;
    if output.write_all(body.as_bytes()).is_err() || output.sync_all().is_err() {
        let _ = std::fs::remove_file(&descriptor);
        return Err("StudyHub could not save the private relink capability.".to_string());
    }
    Ok((relink_id, display_name, metadata.len()))
}

#[tauri::command]
async fn choose_restore_relink_file(
    app: tauri::AppHandle,
    state: tauri::State<'_, BackendState>,
    restore_id: String,
    source_stable_id: String,
) -> Result<Option<(String, String, u64)>, String> {
    if !state.config.profile.allows_external_sources() {
        return Err("Demo/Test mode cannot select external restore sources.".to_string());
    }
    let data_dir = state.config.data_dir.clone();
    let profile = state.config.profile;
    tauri::async_runtime::spawn_blocking(move || {
        let selected = app
            .dialog()
            .file()
            .add_filter("Study material", SUPPORTED_ACADEMIC_EXTENSIONS)
            .blocking_pick_file();
        selected
            .map(|path| {
                create_restore_relink_capability(
                    Path::new(&path.to_string()),
                    &data_dir,
                    profile,
                    &restore_id,
                    &source_stable_id,
                )
            })
            .transpose()
    })
    .await
    .map_err(|_| "StudyHub could not open the restore source picker.".to_string())?
}

fn save_backup_export(
    data_dir: &Path,
    filename: &str,
    destination: &Path,
    profile: RuntimeProfile,
) -> Result<(), String> {
    if !profile.allows_external_sources() {
        return Err("Demo/Test mode cannot export backup archives.".to_string());
    }
    let safe_name = Path::new(filename)
        .file_name()
        .map(|value| value.to_string_lossy().to_string())
        .unwrap_or_default();
    if safe_name != filename || safe_name.is_empty() || !safe_name.ends_with(".zip") {
        return Err("Invalid backup filename.".to_string());
    }
    let source = data_dir.join("exports").join(&safe_name);
    let metadata = std::fs::symlink_metadata(&source)
        .map_err(|_| "StudyHub could not locate the exported backup.".to_string())?;
    if !metadata.is_file() {
        return Err("The exported backup is unavailable.".to_string());
    }
    std::fs::copy(&source, destination)
        .map(|_| ())
        .map_err(|_| "StudyHub could not save the backup archive.".to_string())
}

#[tauri::command]
async fn export_backup_archive(
    app: tauri::AppHandle,
    state: tauri::State<'_, BackendState>,
    filename: String,
) -> Result<Option<String>, String> {
    if !state.config.profile.allows_external_sources() {
        return Err("Demo/Test mode cannot export backup archives.".to_string());
    }
    let data_dir = state.config.data_dir.clone();
    let profile = state.config.profile;
    tauri::async_runtime::spawn_blocking(move || {
        let Some(destination) = app
            .dialog()
            .file()
            .set_file_name(&filename)
            .add_filter("StudyHub backup", &["zip"])
            .blocking_save_file()
        else {
            return Ok(None);
        };
        save_backup_export(&data_dir, &filename, &destination, profile)
            .map(|()| Some(destination.to_string()))
    })
    .await
    .map_err(|_| "StudyHub could not open the save dialog.".to_string())?
}

#[tauri::command]
async fn restart_backend(
    app: tauri::AppHandle,
    state: tauri::State<'_, BackendState>,
) -> Result<(), String> {
    let url = {
        let mut process = state
            .process
            .lock()
            .map_err(|_| "StudyHub local service state is unavailable.".to_string())?;
        if let Some(active) = process.as_mut() {
            stop_child(&mut active.child);
        }
        let canvas_manager = Arc::clone(&state.canvas_manager);
        match start_backend(&state.config, &canvas_manager) {
            Ok(next) => {
                state
                    .allowed_port
                    .store(parse_port(&next.url)?, Ordering::SeqCst);
                let url = next.url.clone();
                *process = Some(next);
                url
            }
            Err(code) => {
                *process = None;
                if let Ok(mut error) = state.last_error.lock() {
                    *error = code;
                }
                navigate_to_fallback(&app);
                return Err("StudyHub could not restart its local service.".to_string());
            }
        }
    };
    let parsed = url
        .parse()
        .map_err(|_| "StudyHub returned an invalid local URL.".to_string())?;
    app.get_webview_window("main")
        .ok_or_else(|| "StudyHub window is unavailable.".to_string())?
        .navigate(parsed)
        .map_err(|_| "StudyHub could not reopen its local workspace.".to_string())
}

#[tauri::command]
async fn retry_backend(
    app: tauri::AppHandle,
    state: tauri::State<'_, BackendState>,
) -> Result<(), String> {
    let mut process = state
        .process
        .lock()
        .map_err(|_| "StudyHub local service state is unavailable.".to_string())?;
    if process.is_some() {
        return Ok(());
    }
    let canvas_manager = Arc::clone(&state.canvas_manager);
    match start_backend(&state.config, &canvas_manager) {
        Ok(next) => {
            let parsed = next
                .url
                .parse()
                .map_err(|_| "StudyHub returned an invalid local URL.".to_string())?;
            state
                .allowed_port
                .store(parse_port(&next.url)?, Ordering::SeqCst);
            *process = Some(next);
            if let Ok(mut error) = state.last_error.lock() {
                error.clear();
            }
            app.get_webview_window("main")
                .ok_or_else(|| "StudyHub window is unavailable.".to_string())?
                .navigate(parsed)
                .map_err(|_| "StudyHub could not open its local workspace.".to_string())
        }
        Err(code) => {
            if let Ok(mut error) = state.last_error.lock() {
                *error = code;
            }
            Err("StudyHub could not start its local service.".to_string())
        }
    }
}

#[tauri::command]
fn startup_diagnostics(state: tauri::State<'_, BackendState>) -> String {
    let status = state
        .last_error
        .lock()
        .ok()
        .map(|code| safe_error_message(&code).to_string())
        .unwrap_or_else(|| "Diagnostics unavailable.".to_string());
    format!(
        "StudyHub Local {}\nProfile: {}\nOS: {}\nArchitecture: {}\nPackaged backend: {}\nStatus: {}",
        env!("CARGO_PKG_VERSION"),
        state.config.profile.environment_name(),
        env::consts::OS,
        env::consts::ARCH,
        if state.config.packaged {
            "yes"
        } else {
            "development"
        },
        status
    )
}

#[tauri::command]
fn canvas_connection_status(state: tauri::State<'_, BackendState>) -> Result<String, String> {
    Ok(canvas_status_json(&state.canvas_manager.status()))
}

#[tauri::command]
fn configure_canvas_development_connection(
    state: tauri::State<'_, BackendState>,
    origin: String,
    access_token: String,
) -> Result<String, String> {
    if state.config.profile != RuntimeProfile::Development {
        return Err("canvas_manual_token_development_only".to_string());
    }
    let store = credential_store_for_profile(state.config.profile);
    configure_canvas_development_connection_in_store(
        state.config.profile,
        &store,
        &origin,
        &access_token,
    )
    .map(|()| "configured".to_string())
    .map_err(|error| error.to_string())
}

#[tauri::command]
fn provision_canvas_oauth_client(
    state: tauri::State<'_, BackendState>,
    origin: String,
    client_id: String,
    client_secret: String,
    redirect_uri: String,
) -> Result<String, String> {
    if state.config.profile != RuntimeProfile::Development {
        return Err("canvas_oauth_client_development_only".to_string());
    }
    state
        .canvas_manager
        .provision_client_config(&origin, &client_id, &client_secret, &redirect_uri)
        .map(|()| "provisioned".to_string())
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn start_canvas_oauth(state: tauri::State<'_, BackendState>) -> Result<String, String> {
    let start: CanvasOAuthStart = state
        .canvas_manager
        .begin_oauth()
        .map_err(|error| error.to_string())?;
    open_in_system_browser(&start.auth_url)?;
    Ok(serde_json::json!({
        "status": CanvasConnectionState::Connecting.as_str(),
        "needsCodeInput": start.needs_code_input,
    })
    .to_string())
}

#[tauri::command]
fn cancel_canvas_oauth(state: tauri::State<'_, BackendState>) -> Result<String, String> {
    state.canvas_manager.cancel_oauth();
    Ok(canvas_status_json(&state.canvas_manager.status()))
}

#[tauri::command]
fn complete_canvas_oauth(
    state: tauri::State<'_, BackendState>,
    response_url: String,
) -> Result<String, String> {
    let status = state
        .canvas_manager
        .complete_oauth(&response_url)
        .map_err(|error| error.to_string())?;
    Ok(canvas_status_json(&status))
}

#[tauri::command]
fn disconnect_canvas(
    state: tauri::State<'_, BackendState>,
    revoke: Option<bool>,
) -> Result<String, String> {
    let result = state
        .canvas_manager
        .disconnect(revoke.unwrap_or(false))
        .map_err(|error| error.to_string())?;
    let status = state.canvas_manager.status();
    Ok(serde_json::json!({
        "status": status.state.as_str(),
        "remoteRevoked": result.remote_revoked,
        "remoteRevokeAttempted": result.remote_revoke_attempted,
        "remoteRevokeFailed": result.remote_revoke_failed,
    })
    .to_string())
}

fn canvas_status_json(status: &crate::canvas_oauth::CanvasStatus) -> String {
    serde_json::json!({
        "status": status.state.as_str(),
        "authMode": status.auth_mode,
        "origin": status.origin,
        "displayName": status.display_name,
    })
    .to_string()
}

fn open_in_system_browser(url: &str) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let status = Command::new("/usr/bin/open")
            .arg(url)
            .status()
            .map_err(|_| "canvas_browser_open_failed".to_string())?;
        if status.success() {
            Ok(())
        } else {
            Err("canvas_browser_open_failed".to_string())
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = url;
        Err("canvas_browser_unavailable".to_string())
    }
}

#[tauri::command]
fn deadline_notification_status(state: tauri::State<'_, BackendState>) -> Result<String, String> {
    let enabled = *state
        .notification_enabled
        .lock()
        .map_err(|_| "StudyHub notification state is unavailable.".to_string())?;
    Ok(serde_json::json!({ "enabled": enabled, "permission": "granted" }).to_string())
}

#[tauri::command]
fn set_deadline_notifications_enabled(
    state: tauri::State<'_, BackendState>,
    enabled: bool,
) -> Result<String, String> {
    let preference_path = state.config.data_dir.join("notification-preference");
    save_notification_preference(&preference_path, enabled)?;
    *state
        .notification_enabled
        .lock()
        .map_err(|_| "StudyHub notification state is unavailable.".to_string())? = enabled;
    Ok(serde_json::json!({ "enabled": enabled }).to_string())
}

#[tauri::command]
fn evaluate_deadline_notifications(
    app: tauri::AppHandle,
    state: tauri::State<'_, BackendState>,
    events: Vec<DeadlineNotificationEvent>,
) -> Result<String, String> {
    if events.len() > 64 {
        return Err("canvas_notification_input_too_large".to_string());
    }
    for event in &events {
        if event.stable_id.len() > 256 || event.revision.len() > 256 || event.course_code.len() > 64 || event.due_at.len() > 64 {
            return Err("canvas_notification_input_invalid".to_string());
        }
    }
    let enabled = *state
        .notification_enabled
        .lock()
        .map_err(|_| "StudyHub notification state is unavailable.".to_string())?;
    let ledger_path = state.config.data_dir.join("notification-ledger.txt");
    let policy = DeadlineNotificationPolicy::new(enabled, load_delivery_ledger(&ledger_path));
    let to_send = policy.evaluate(&events);
    let sink = OsNotificationSink::new(app);
    let sent = dispatch_deadline_notifications(&sink, &ledger_path, &to_send)
        .map_err(|_| "notification_ledger_write_failed".to_string())?;
    Ok(serde_json::json!({ "sent": sent }).to_string())
}

#[tauri::command]
fn open_canvas_url(state: tauri::State<'_, BackendState>, url: String) -> Result<(), String> {
    let origin = state
        .canvas_manager
        .current_canvas_origin()
        .ok_or_else(|| "canvas_connection_missing".to_string())?;
    if !canvas_url_matches_origin(&url, origin.as_str()) {
        return Err("canvas_invalid_url".to_string());
    }
    open_in_system_browser(&url)
}

fn canvas_url_matches_origin(url: &str, origin: &str) -> bool {
    const MAX_URL_BYTES: usize = 8192;
    if url.is_empty() || url.len() > MAX_URL_BYTES {
        return false;
    }
    let Ok(parsed) = tauri::Url::parse(url) else {
        return false;
    };
    let Ok(origin_url) = tauri::Url::parse(origin) else {
        return false;
    };
    parsed.scheme() == "https"
        && origin_url.scheme() == "https"
        && parsed.username().is_empty()
        && parsed.password().is_none()
        && parsed.fragment().is_none()
        && parsed.host_str().map(|host| host.to_ascii_lowercase())
            == origin_url.host_str().map(|host| host.to_ascii_lowercase())
        && parsed.port_or_known_default() == origin_url.port_or_known_default()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            choose_study_folder,
            choose_study_files,
            choose_backup_archive,
            choose_restore_relink_file,
            export_backup_archive,
            canvas_connection_status,
            configure_canvas_development_connection,
            provision_canvas_oauth_client,
            start_canvas_oauth,
            cancel_canvas_oauth,
            complete_canvas_oauth,
            disconnect_canvas,
            deadline_notification_status,
            set_deadline_notifications_enabled,
            evaluate_deadline_notifications,
            open_canvas_url,
            restart_backend,
            retry_backend,
            startup_diagnostics
        ])
        .setup(|app| {
            let config = backend_config(app).map_err(std::io::Error::other)?;
            let window_title = config.profile.display_name();
            let webview_data_dir = config.webview_data_dir.clone();
            let allowed_port = Arc::new(AtomicU16::new(0));
            let canvas_manager = Arc::new(CanvasCredentialManager::for_profile(config.profile));
            let process = start_backend(&config, &canvas_manager);
            let (initial_url, initial_error) = match process.as_ref() {
                Ok(active) => {
                    allowed_port.store(
                        parse_port(&active.url).map_err(std::io::Error::other)?,
                        Ordering::SeqCst,
                    );
                    (WebviewUrl::External(active.url.parse()?), String::new())
                }
                Err(code) => (WebviewUrl::App("index.html".into()), code.clone()),
            };
            app.manage(BackendState {
                notification_enabled: Mutex::new(load_notification_preference(
                    &config.data_dir.join("notification-preference"),
                )),
                config,
                process: Mutex::new(process.ok()),
                allowed_port: allowed_port.clone(),
                last_error: Mutex::new(initial_error),
                canvas_manager,
            });

            let mut window_builder = WebviewWindowBuilder::new(app, "main", initial_url)
                .title(window_title)
                .inner_size(1280.0, 820.0)
                .min_inner_size(900.0, 620.0)
                .devtools(cfg!(debug_assertions))
                .on_navigation(move |candidate| {
                    let fallback =
                        candidate.scheme() == "tauri" && candidate.host_str() == Some("localhost");
                    let backend = candidate.scheme() == "http"
                        && candidate.host_str() == Some("localhost")
                        && candidate.port_or_known_default()
                            == Some(allowed_port.load(Ordering::SeqCst));
                    fallback || backend
                });
            if let Some(directory) = webview_data_dir {
                std::fs::create_dir_all(&directory)?;
                window_builder = window_builder.data_directory(directory);
            }
            window_builder.build()?;
            start_backend_monitor(app.handle().clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build StudyHub Local desktop prototype");

    app.run(|handle, event| {
        if matches!(event, RunEvent::Exit) {
            if let Some(state) = handle.try_state::<BackendState>() {
                if let Ok(mut process) = state.process.lock() {
                    if let Some(active) = process.as_mut() {
                        stop_child(&mut active.child);
                    }
                    *process = None;
                }
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn production_requires_the_production_identifier_and_release_build() {
        assert_eq!(
            resolve_runtime_profile(PRODUCTION_IDENTIFIER, false, false),
            Ok(RuntimeProfile::Production)
        );
        assert!(resolve_runtime_profile(PRODUCTION_IDENTIFIER, true, false).is_err());
    }

    #[test]
    fn development_and_demo_require_distinct_debug_identifiers() {
        assert_eq!(
            resolve_runtime_profile(DEVELOPMENT_IDENTIFIER, true, false),
            Ok(RuntimeProfile::Development)
        );
        assert_eq!(
            resolve_runtime_profile(DEMO_IDENTIFIER, true, false),
            Ok(RuntimeProfile::DemoTest)
        );
        assert!(resolve_runtime_profile(DEVELOPMENT_IDENTIFIER, false, false).is_err());
    }

    #[test]
    fn explicit_test_root_always_selects_demo_test() {
        assert_eq!(
            resolve_runtime_profile(PRODUCTION_IDENTIFIER, true, true),
            Ok(RuntimeProfile::DemoTest)
        );
    }

    #[test]
    fn native_backup_staging_preserves_original_and_uses_profile_inbox() {
        let root = env::temp_dir().join(format!("studyhub-restore-stage-{}", next_restore_id()));
        let source = root.join("synthetic-backup.zip");
        let data_dir = root.join("profile-data");
        std::fs::create_dir_all(&root).expect("create synthetic staging root");
        let original = b"PK\x05\x06synthetic-backup";
        std::fs::write(&source, original).expect("write synthetic archive");
        let restore_id = stage_backup_archive(&source, &data_dir, RuntimeProfile::Development)
            .expect("stage synthetic archive");
        assert_eq!(restore_id.len(), 32);
        assert!(restore_id.chars().all(|value| value.is_ascii_hexdigit()));
        assert_eq!(std::fs::read(&source).expect("read original"), original);
        assert_eq!(
            std::fs::read(
                data_dir
                    .join("restore-inbox")
                    .join(format!("{restore_id}.zip"))
            )
            .expect("read staged copy"),
            original
        );
        std::fs::remove_dir_all(root).expect("remove synthetic staging root");
    }

    #[test]
    fn demo_test_backup_staging_is_denied_before_copy() {
        let root = env::temp_dir().join(format!("studyhub-restore-deny-{}", next_restore_id()));
        let source = root.join("synthetic-backup.zip");
        std::fs::create_dir_all(&root).expect("create synthetic deny root");
        std::fs::write(&source, b"PK\x05\x06synthetic-backup").expect("write synthetic archive");
        assert!(stage_backup_archive(
            &source,
            &root.join("profile-data"),
            RuntimeProfile::DemoTest
        )
        .is_err());
        assert!(!root.join("profile-data/restore-inbox").exists());
        std::fs::remove_dir_all(root).expect("remove synthetic deny root");
    }

    #[test]
    fn native_backup_export_copies_profile_archive_to_chosen_path() {
        let root = env::temp_dir().join(format!("studyhub-export-{}", next_restore_id()));
        let data_dir = root.join("profile-data");
        let exports = data_dir.join("exports");
        std::fs::create_dir_all(&exports).expect("create synthetic export root");
        let bytes = b"PK\x05\x06synthetic-backup-export";
        std::fs::write(exports.join("studyhub-backup-v2-test.zip"), bytes).expect("write export");
        let destination = root.join("Saved Backup.zip");
        save_backup_export(
            &data_dir,
            "studyhub-backup-v2-test.zip",
            &destination,
            RuntimeProfile::Development,
        )
        .expect("copy export");
        assert_eq!(std::fs::read(&destination).expect("read copy"), bytes);
        std::fs::remove_dir_all(root).expect("remove synthetic export root");
    }

    #[test]
    fn native_backup_export_rejects_unsafe_filename_and_demo_profile() {
        let root = env::temp_dir().join(format!("studyhub-export-deny-{}", next_restore_id()));
        let data_dir = root.join("profile-data");
        std::fs::create_dir_all(data_dir.join("exports")).expect("create synthetic export root");
        assert!(save_backup_export(
            &data_dir,
            "../escape.zip",
            &root.join("out.zip"),
            RuntimeProfile::Development
        )
        .is_err());
        assert!(save_backup_export(
            &data_dir,
            "backup.zip",
            &root.join("out.zip"),
            RuntimeProfile::DemoTest
        )
        .is_err());
        assert!(!root.join("out.zip").exists());
        std::fs::remove_dir_all(root).expect("remove synthetic export root");
    }

    #[test]
    fn native_relink_capability_is_opaque_private_and_non_mutating() {
        let root = env::temp_dir().join(format!("studyhub-relink-stage-{}", next_restore_id()));
        let source = root.join("replacement.pdf");
        let data_dir = root.join("profile-data");
        std::fs::create_dir_all(&root).expect("create synthetic relink root");
        let original = b"synthetic academic bytes";
        std::fs::write(&source, original).expect("write synthetic source");
        let (relink_id, display_name, byte_size) = create_restore_relink_capability(
            &source,
            &data_dir,
            RuntimeProfile::Development,
            "0123456789abcdef0123456789abcdef",
            "source:test-relink",
        )
        .expect("create relink capability");
        assert_eq!(relink_id.len(), 32);
        assert_eq!(display_name, "replacement.pdf");
        assert_eq!(byte_size, original.len() as u64);
        assert_eq!(std::fs::read(&source).expect("read original"), original);
        let descriptor = data_dir
            .join("restore-relink-inbox")
            .join(format!("{relink_id}.json"));
        let descriptor_text = std::fs::read_to_string(&descriptor).expect("read descriptor");
        assert!(descriptor_text.contains("source:test-relink"));
        assert!(descriptor_text.contains("replacement.pdf"));
        #[cfg(unix)]
        assert_eq!(
            std::fs::metadata(&descriptor)
                .expect("descriptor metadata")
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        std::fs::remove_dir_all(root).expect("remove synthetic relink root");
    }

    #[test]
    fn demo_test_relink_capability_is_denied_before_descriptor_creation() {
        let root = env::temp_dir().join(format!("studyhub-relink-deny-{}", next_restore_id()));
        let source = root.join("replacement.pdf");
        let data_dir = root.join("profile-data");
        std::fs::create_dir_all(&root).expect("create synthetic relink deny root");
        std::fs::write(&source, b"synthetic academic bytes").expect("write synthetic source");
        assert!(create_restore_relink_capability(
            &source,
            &data_dir,
            RuntimeProfile::DemoTest,
            "0123456789abcdef0123456789abcdef",
            "source:test-relink",
        )
        .is_err());
        assert!(!data_dir.join("restore-relink-inbox").exists());
        std::fs::remove_dir_all(root).expect("remove synthetic relink deny root");
    }

    #[test]
    fn canvas_url_matches_origin_is_exact() {
        let origin = "https://canvas.example.edu";
        assert!(canvas_url_matches_origin("https://canvas.example.edu/courses/1/assignments/2", origin));
        assert!(!canvas_url_matches_origin("https://canvas.example.edu:8443/x", origin));
        assert!(!canvas_url_matches_origin("https://other.example.edu/x", origin));
        assert!(!canvas_url_matches_origin("http://canvas.example.edu/x", origin));
        let userinfo = format!("https://u:{}@canvas.example.edu/x", "p");
        assert!(!canvas_url_matches_origin(&userinfo, origin));
        assert!(!canvas_url_matches_origin("https://canvas.example.edu/x#frag", origin));
        assert!(!canvas_url_matches_origin(&format!("https://canvas.example.edu/{}", "x".repeat(9000)), origin));
    }
}
