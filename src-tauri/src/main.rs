#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Workbench Tauri 2 shell.
//!
//! Phase 1 目标：把旧 Electron 壳的“窗口 + 启动 Python sidecar”这一段用 Rust 重写，
//! 业务仍完全由 FastAPI 提供。窗口先显示本地 splash，健康检查通过后导航到
//! `http://127.0.0.1:<port>/`；sidecar 的父进程 PID 会下发给 Python 的
//! `lifecycle.watch_parent()`，Tauri 进程异常死亡时 sidecar 自己退出。
//!
//! 自动更新由 `tauri-plugin-updater` 托管；更新源是内部 HTTPS 静态 `latest.json`。
//! 本文件不直接调用 updater：先搭好签名与配置骨架，界面/更新交互在 Phase 1 后半段接。

use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;

/// Python sidecar 句柄。退出时必须杀掉整棵进程树，不能只 `Child::kill()`
/// （Python 还会拉起 MCP / 沙箱子进程；父进程死亡后 `lifecycle.py` 负责兜底）。
struct SidecarState {
    child: Mutex<Option<Child>>,
}

fn free_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0)).map_err(|e| format!("选空闲端口失败：{e}"))?;
    let port = listener.local_addr().map_err(|e| format!("读端口失败：{e}"))?.port();
    drop(listener);
    Ok(port)
}

fn data_dir() -> PathBuf {
    if let Ok(value) = std::env::var("WORKBENCH_DATA") {
        if !value.trim().is_empty() {
            return PathBuf::from(value);
        }
    }
    let appdata = std::env::var("APPDATA").unwrap_or_default();
    PathBuf::from(appdata).join("Workbench")
}

fn sidecar_log() -> Stdio {
    let dir = data_dir().join("logs");
    let _ = std::fs::create_dir_all(&dir);
    match OpenOptions::new().create(true).append(true).open(dir.join("tauri-sidecar.log")) {
        Ok(file) => Stdio::from(file),
        Err(_) => Stdio::null(),
    }
}

/// 开发态：仓库根目录的 `.venv` + `python -m workbench.server_entry`。
/// 打包态：Tauri resource 里的 `server/WorkbenchServer.exe`（或兼容目录名）。
fn server_invocation(app: &AppHandle, port: u16) -> Result<(PathBuf, Vec<String>, PathBuf), String> {
    let _ = app;
    #[cfg(debug_assertions)]
    {
        use std::path::Path;
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .ok_or_else(|| "找不到仓库根目录".to_string())?
            .to_path_buf();
        let python = root.join(".venv").join("Scripts").join("python.exe");
        if !python.is_file() {
            return Err(format!(
                "开发态找不到 Python：{}（先执行 uv sync --extra dev）",
                python.display()
            ));
        }
        let args = vec![
            "-m".into(),
            "workbench.server_entry".into(),
            "--server".into(),
            "--host".into(),
            "127.0.0.1".into(),
            "--port".into(),
            port.to_string(),
        ];
        return Ok((python, args, root));
    }

    #[cfg(not(debug_assertions))]
    {
        let resource_dir = app
            .path()
            .resource_dir()
            .map_err(|e| format!("读 Tauri resource 目录失败：{e}"))?;
        let candidates = [
            resource_dir.join("server").join("WorkbenchServer.exe"),
            resource_dir.join("WorkbenchServer").join("WorkbenchServer.exe"),
            resource_dir.join("server").join("WorkbenchServer").join("WorkbenchServer.exe"),
        ];
        for exe in candidates {
            if exe.is_file() {
                let cwd = exe.parent().unwrap_or(&resource_dir).to_path_buf();
                let args = vec![
                    "--server".into(),
                    "--host".into(),
                    "127.0.0.1".into(),
                    "--port".into(),
                    port.to_string(),
                ];
                return Ok((exe, args, cwd));
            }
        }
        Err("打包态找不到 server/WorkbenchServer.exe；检查 bundle.resources".to_string())
    }
}

fn spawn_sidecar(app: &AppHandle, port: u16) -> Result<Child, String> {
    let (exe, args, cwd) = server_invocation(app, port)?;
    let mut cmd = Command::new(exe);
    cmd.args(args)
        .current_dir(cwd)
        .env("WORKBENCH_HOST", "127.0.0.1")
        .env("WORKBENCH_PORT", port.to_string())
        // Python stdout/stderr 在 Windows 默认 GBK；统一 UTF-8，避免 sidecar 日志乱码。
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        // Python 的父进程监视：Tauri 被强杀时 sidecar 也必须退出。
        .env("WORKBENCH_PARENT_PID", std::process::id().to_string())
        .stdin(Stdio::null())
        .stdout(sidecar_log())
        .stderr(sidecar_log());

    // 不让 sidecar 在开发机/用户桌面弹出一个黑框。
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    cmd.spawn().map_err(|e| format!("启动 Python sidecar 失败：{e}"))
}

/// 不引入 HTTP 客户端依赖：健康检查只需要一个最小 GET，且必须短超时。
fn health_once(port: u16) -> bool {
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
    let _ = stream.write_all(
        b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n",
    );
    let mut buf = String::new();
    let _ = stream.read_to_string(&mut buf);
    buf.starts_with("HTTP/1.1 200") || buf.starts_with("HTTP/1.0 200")
}

fn wait_health(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if health_once(port) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(300));
    }
    health_once(port)
}

fn is_local_url(url: &url::Url) -> bool {
    if matches!(url.scheme(), "tauri") {
        return true;
    }
    if !matches!(url.scheme(), "http" | "https") {
        return false;
    }
    // Tauri 在 Windows 上把本地资源映射到 `http://tauri.localhost/`；
    // 漏掉它会让启动页/主窗每次导航都走 on_navigation 的“外部链接”分支，
    // 于是点桌面图标会先弹出一个 Edge 标签页（2026-10-20 实测）。
    matches!(
        url.host_str(),
        Some("127.0.0.1") | Some("localhost") | Some("tauri.localhost")
    )
}

fn window_geometry(app: &AppHandle) -> (f64, f64, f64, f64) {
    // 默认窗口：主显示器工作区的 80%，居中。不要最大化，也不要固定 1440×920
    // （小屏/高缩放下会超出可视区域，用户看不到完整内容）。
    let fallback = (1152.0, 720.0, 144.0, 90.0); // 1440×900 工作区的 80%
    let Ok(Some(monitor)) = app.primary_monitor() else {
        return fallback;
    };
    let scale = monitor.scale_factor();
    if scale <= 0.0 {
        return fallback;
    }
    let area = monitor.work_area();
    let area_w = area.size.width as f64 / scale;
    let area_h = area.size.height as f64 / scale;
    if area_w <= 0.0 || area_h <= 0.0 {
        return fallback;
    }
    let width = area_w * 0.8;
    let height = area_h * 0.8;
    let x = area.position.x as f64 / scale + (area_w - width) / 2.0;
    let y = area.position.y as f64 / scale + (area_h - height) / 2.0;
    (width, height, x, y)
}

fn create_splash(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let (width, height, x, y) = window_geometry(app);
    let navigation_app = app.clone();
    let new_window_app = app.clone();
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("Workbench")
        .inner_size(width, height)
        .position(x, y)
        .min_inner_size(480.0, 360.0)
        .resizable(true)
        // 只允许本地服务/本地资产在壳内导航；外部链接交给系统浏览器。
        .on_navigation(move |url| {
            if is_local_url(url) {
                return true;
            }
            let _ = navigation_app
                .opener()
                .open_url(url.as_str(), None::<&str>);
            false
        })
        .on_new_window(move |url, _features| {
            if !is_local_url(&url) {
                let _ = new_window_app
                    .opener()
                    .open_url(url.as_str(), None::<&str>);
            }
            tauri::webview::NewWindowResponse::Deny
        })
        .build()?;
    Ok(())
}

fn stop_sidecar(app: &AppHandle) {
    let Some(state) = app.try_state::<SidecarState>() else {
        return;
    };
    let Ok(mut guard) = state.child.lock() else {
        return;
    };
    let Some(mut child) = guard.take() else {
        return;
    };
    let pid = child.id();

    // Windows 上先按进程树杀：Python 还会拉起 MCP / 沙箱子进程。
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let _ = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .status();
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn main() {
    let app = tauri::Builder::default()
        // 单实例必须尽早注册；第二个实例只把已有主窗拉到前台。
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            create_splash(app.handle())?;

            let port = free_port().map_err(|e| Box::<dyn std::error::Error>::from(e))?;
            let child = spawn_sidecar(app.handle(), port)
                .map_err(|e| Box::<dyn std::error::Error>::from(e))?;
            app.manage(SidecarState {
                child: Mutex::new(Some(child)),
            });

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let healthy = wait_health(port, Duration::from_secs(40));
                let handle_for_ui = handle.clone();
                let _ = handle.run_on_main_thread(move || {
                    let Some(window) = handle_for_ui.get_webview_window("main") else {
                        return;
                    };
                    if healthy {
                        let Ok(url) = format!("http://127.0.0.1:{port}/?shell=tauri").parse::<url::Url>() else {
                            return;
                        };
                        let _ = window.navigate(url);
                        let _ = window.set_title("Workbench");
                    } else {
                        let message = serde_json::to_string(
                            "本地服务启动超时（40 秒）。请查看 %APPDATA%\\Workbench\\logs\\tauri-sidecar.log。",
                        )
                        .unwrap_or_else(|_| "\"本地服务启动超时\"".to_string());
                        let _ = window.eval(format!(
                            "document.getElementById('msg').textContent = {message};"
                        ));
                    }
                });
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("构建 Tauri 应用失败");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit) {
            stop_sidecar(app_handle);
        }
    });
}
