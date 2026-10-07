use std::env;
use std::ffi::OsString;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
#[cfg(debug_assertions)]
use std::path::Path;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU16, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

#[cfg(not(debug_assertions))]
use tauri::path::BaseDirectory;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;

mod canvas_connection;
mod credential_handoff;
mod credential_store;
mod credential_transport;

use crate::canvas_connection::{
    canvas_connection_status_in_store, configure_canvas_development_connection_in_store,
    remove_canvas_connection_in_store,
};
use crate::credential_store::{credential_store_for_profile, CredentialAvailability};

const STARTUP_TIMEOUT: Duration = Duration::from_secs(30);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(4);
const FALLBACK_URL: &str = "tauri://localhost/index.html";
const PRODUCTION_IDENTIFIER: &str = "io.studyhublocal.desktop";
const DEVELOPMENT_IDENTIFIER: &str = "io.studyhublocal.desktop.dev";
const DEMO_IDENTIFIER: &str = "io.studyhublocal.desktop.demo";

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
    let (executable, prefix_args, packaged, static_dir, katex_dir) = {
        let root = development_root();
        (
            PathBuf::from(
                env::var("STUDYHUB_DESKTOP_PYTHON").unwrap_or_else(|_| "python3".to_string()),
            ),
            vec![root.join("server.py").into_os_string()],
            false,
            root.join("static"),
            root.join("node_modules/katex/dist"),
        )
    };

    #[cfg(not(debug_assertions))]
    let (executable, prefix_args, packaged, static_dir, katex_dir) = (
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
    );

    Ok(BackendConfig {
        executable,
        prefix_args,
        packaged,
        static_dir,
        katex_dir,
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

fn start_backend(config: &BackendConfig) -> Result<BackendProcess, String> {
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
        credential_transport::spawn_backend_credential_transport(
            transport,
            child.id(),
            config.profile,
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
        match start_backend(&state.config) {
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
    match start_backend(&state.config) {
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
    let store = credential_store_for_profile(state.config.profile);
    match canvas_connection_status_in_store(&store) {
        Ok(CredentialAvailability::Configured) => Ok("configured".to_string()),
        Ok(CredentialAvailability::Missing) => Ok("missing".to_string()),
        Err(error) => Err(error.to_string()),
    }
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
fn remove_canvas_connection(state: tauri::State<'_, BackendState>) -> Result<String, String> {
    let store = credential_store_for_profile(state.config.profile);
    remove_canvas_connection_in_store(&store)
        .map(|()| "removed".to_string())
        .map_err(|error| error.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            choose_study_folder,
            choose_study_files,
            canvas_connection_status,
            configure_canvas_development_connection,
            remove_canvas_connection,
            restart_backend,
            retry_backend,
            startup_diagnostics
        ])
        .setup(|app| {
            let config = backend_config(app).map_err(std::io::Error::other)?;
            let window_title = config.profile.display_name();
            let webview_data_dir = config.webview_data_dir.clone();
            let allowed_port = Arc::new(AtomicU16::new(0));
            let process = start_backend(&config);
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
                config,
                process: Mutex::new(process.ok()),
                allowed_port: allowed_port.clone(),
                last_error: Mutex::new(initial_error),
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
}
