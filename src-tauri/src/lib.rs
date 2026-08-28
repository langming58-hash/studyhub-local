use std::env;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
#[cfg(debug_assertions)]
use std::path::Path;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

#[cfg(not(debug_assertions))]
use tauri::path::BaseDirectory;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;

const STARTUP_TIMEOUT: Duration = Duration::from_secs(30);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(4);

#[derive(Clone)]
struct BackendConfig {
    python: String,
    server: PathBuf,
    static_dir: PathBuf,
    demo_dir: PathBuf,
    katex_dir: PathBuf,
    runtime_dir: PathBuf,
    config_path: PathBuf,
}

struct BackendProcess {
    child: Child,
    url: String,
}

struct BackendState {
    config: BackendConfig,
    process: Mutex<BackendProcess>,
}

#[cfg(debug_assertions)]
fn development_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri must live inside the project")
        .to_path_buf()
}

fn backend_config(app: &tauri::App) -> Result<BackendConfig, String> {
    let app_data = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?;
    let app_config = app
        .path()
        .app_config_dir()
        .map_err(|error| error.to_string())?;
    let python = env::var("STUDYHUB_DESKTOP_PYTHON").unwrap_or_else(|_| "python3".to_string());

    #[cfg(debug_assertions)]
    let (server, static_dir, demo_dir, katex_dir) = {
        let root = development_root();
        (
            root.join("server.py"),
            root.join("static"),
            root.join("demo-data"),
            root.join("node_modules/katex/dist"),
        )
    };

    #[cfg(not(debug_assertions))]
    let (server, static_dir, demo_dir, katex_dir) = (
        app.path()
            .resolve("server.py", BaseDirectory::Resource)
            .map_err(|error| error.to_string())?,
        app.path()
            .resolve("static", BaseDirectory::Resource)
            .map_err(|error| error.to_string())?,
        app.path()
            .resolve("demo-data", BaseDirectory::Resource)
            .map_err(|error| error.to_string())?,
        app.path()
            .resolve("katex", BaseDirectory::Resource)
            .map_err(|error| error.to_string())?,
    );

    Ok(BackendConfig {
        python,
        server,
        static_dir,
        demo_dir,
        katex_dir,
        runtime_dir: app_data,
        config_path: app_config.join("settings.env"),
    })
}

fn parse_port(url: &str) -> Result<u16, String> {
    url.rsplit_once(':')
        .and_then(|(_, port)| port.parse::<u16>().ok())
        .ok_or_else(|| "StudyHub returned an invalid localhost URL".to_string())
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
    std::fs::create_dir_all(&config.runtime_dir).map_err(|error| error.to_string())?;
    if let Some(parent) = config.config_path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }

    let mut command = Command::new(&config.python);
    command
        .arg(&config.server)
        .args(["serve", "--port", "0"])
        .env("HOST", "127.0.0.1")
        .env("STUDYHUB_DESKTOP", "true")
        .env("STUDYHUB_RUNTIME_DIR", &config.runtime_dir)
        .env("STUDYHUB_CONFIG_PATH", &config.config_path)
        .env("STUDYHUB_STATIC_DIR", &config.static_dir)
        .env("STUDYHUB_DEMO_DATA_DIR", &config.demo_dir)
        .env("STUDYHUB_KATEX_DIR", &config.katex_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());

    let mut child = command
        .spawn()
        .map_err(|error| format!("Could not start the StudyHub backend: {error}"))?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "Backend output was unavailable".to_string())?;
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let mut sent = false;
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if !sent {
                if let Some(url) = line.strip_prefix("StudyHub Local running at ") {
                    let _ = sender.send(url.trim().to_string());
                    sent = true;
                }
            }
        }
    });

    let url = match receiver.recv_timeout(STARTUP_TIMEOUT) {
        Ok(url) => url,
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err("StudyHub backend did not report a localhost URL in time".to_string());
        }
    };

    let deadline = Instant::now() + STARTUP_TIMEOUT;
    while Instant::now() < deadline {
        if health_check(&url) {
            return Ok(BackendProcess { child, url });
        }
        if child
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_some()
        {
            return Err("StudyHub backend exited before its health check passed".to_string());
        }
        thread::sleep(Duration::from_millis(150));
    }

    stop_child(&mut child);
    Err("StudyHub backend health check timed out".to_string())
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

#[tauri::command]
fn choose_study_folder(app: tauri::AppHandle) -> Result<Option<String>, String> {
    Ok(app
        .dialog()
        .file()
        .blocking_pick_folder()
        .map(|path| path.to_string()))
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
            .map_err(|_| "Backend state is unavailable".to_string())?;
        stop_child(&mut process.child);
        *process = start_backend(&state.config)?;
        process.url.clone()
    };
    let parsed = url
        .parse()
        .map_err(|error| format!("Invalid backend URL: {error}"))?;
    app.get_webview_window("main")
        .ok_or_else(|| "StudyHub window is unavailable".to_string())?
        .navigate(parsed)
        .map_err(|error| error.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            choose_study_folder,
            restart_backend
        ])
        .setup(|app| {
            let config = backend_config(app).map_err(std::io::Error::other)?;
            let process = start_backend(&config).map_err(std::io::Error::other)?;
            let url = process.url.clone();
            let allowed_port = parse_port(&url).map_err(std::io::Error::other)?;
            app.manage(BackendState {
                config,
                process: Mutex::new(process),
            });

            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url.parse()?))
                .title("StudyHub Local")
                .inner_size(1280.0, 820.0)
                .min_inner_size(900.0, 620.0)
                .devtools(cfg!(debug_assertions))
                .on_navigation(move |candidate| {
                    candidate.scheme() == "http"
                        && candidate.host_str() == Some("localhost")
                        && candidate.port_or_known_default() == Some(allowed_port)
                })
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build StudyHub Local desktop prototype");

    app.run(|handle, event| {
        if matches!(event, RunEvent::Exit) {
            if let Some(state) = handle.try_state::<BackendState>() {
                if let Ok(mut process) = state.process.lock() {
                    stop_child(&mut process.child);
                }
            }
        }
    });
}
