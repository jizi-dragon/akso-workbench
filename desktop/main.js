// Akso Workbench 桌面壳（Electron）
// 职责：Python 服务 sidecar 生命周期 + 主窗/透明轮盘窗 + 全局热键 Alt+Q
//       + 托盘 + 会话控制服务(18767，供 browser_pool CDP 模式开户窗) + 自动更新
// 架构不变量：窗口只做"壳"，业务全在 FastAPI 服务（HTTP 暴露）；
//       托管会话复用本壳的 Chromium（CDP 18766 ← playwright connect_over_cdp）。

const { app, BrowserWindow, globalShortcut, Tray, Menu } = require('electron');
const http = require('http');
const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const SERVER_PORT = 18765;
const CONTROL_PORT = 18767;
const DEBUG_PORT = 18766;

let serverChild = null;
let mainWindow = null;
let wheelWindow = null;
let tray = null;
let quitting = false;

// 会话窗注册表：windowId → BrowserWindow（browser_pool CDP 模式经控制服务开户窗）
const sessionWindows = new Map();

const serverUrl = (p) => `http://127.0.0.1:${SERVER_PORT}${p}`;

// —— CDP 调试端口（browser_pool connect_over_cdp 用；必须 ready 前设置） ——
app.commandLine.appendSwitch('remote-debugging-port', String(DEBUG_PORT));
app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1');

// ------------------------------------------------------------ 服务 sidecar

function spawnServer() {
  const packaged = process.resourcesPath
    ? path.join(process.resourcesPath, 'server', 'AksoServer.exe')
    : null;
  if (packaged && fs.existsSync(packaged)) {
    serverChild = spawn(packaged, ['--server'], {
      cwd: path.dirname(packaged),
      stdio: 'ignore',
      windowsHide: true,
    });
    return;
  }
  // 开发态回退：仓库 venv 的 uvicorn
  const py = path.join(__dirname, '..', '.venv', 'Scripts', 'python.exe');
  const repoRoot = path.join(__dirname, '..');
  if (fs.existsSync(py)) {
    serverChild = spawn(py,
      ['-m', 'uvicorn', 'workbench.api:app', '--host', '127.0.0.1', '--port', String(SERVER_PORT)],
      { cwd: repoRoot, stdio: 'ignore', windowsHide: true });
    return;
  }
  console.error('未找到服务端（打包资源与 venv 均缺失）');
}

function killServer() {
  if (serverChild && serverChild.pid) {
    try {
      execSync(`taskkill /PID ${serverChild.pid} /T /F`, { windowsHide: true, stdio: 'ignore' });
    } catch { /* 进程可能已退出 */ }
  }
  serverChild = null;
}

function waitReady(timeoutMs = 40000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve) => {
    const tick = () => {
      const req = http.get(serverUrl('/'), { timeout: 2000 }, (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      });
      req.on('error', () => {});
      req.on('timeout', () => { req.destroy(); });
      if (Date.now() < deadline) setTimeout(tick, 300);
      else resolve(false);
    };
    tick();
  });
}

// ------------------------------------------------------------ 窗口

function createMain() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1100,
    minHeight: 700,
    title: 'Akso Workbench',
    show: false,
    backgroundColor: '#eef4fc',
    webPreferences: { backgroundThrottling: false },
  });
  mainWindow.loadURL(serverUrl('/'));
  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.on('closed', () => { mainWindow = null; });
}

function toggleWheel() {
  if (wheelWindow && !wheelWindow.isDestroyed()) {
    wheelWindow.close();
    wheelWindow = null;
    return;
  }
  wheelWindow = new BrowserWindow({
    width: 560,
    height: 640,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    title: 'Akso 轮盘',
    webPreferences: { backgroundThrottling: false },
  });
  wheelWindow.setAlwaysOnTop(true, 'screen-saver');
  wheelWindow.loadURL(serverUrl('/static/pages/wheel-picker.html?transparent=1'));
  wheelWindow.on('closed', () => { wheelWindow = null; });
}

// ------------------------------------------------- 会话控制服务（18767）

function createControlServer() {
  const json = (res, code, body) => {
    res.writeHead(code, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(body));
  };
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      let payload = {};
      try { payload = body ? JSON.parse(body) : {}; } catch { payload = {}; }
      const url = new URL(req.url, 'http://127.0.0.1');
      if (url.pathname === '/health') return json(res, 200, { ok: true });

      if (req.method === 'POST' && url.pathname === '/windows') {
        const windowId = String(payload.windowId || '');
        if (!windowId) return json(res, 400, { error: 'windowId required' });
        if (sessionWindows.has(windowId) && !sessionWindows.get(windowId).isDestroyed()) {
          return json(res, 200, { ok: true, reused: true });
        }
        const win = new BrowserWindow({
          width: 1440,
          height: 900,
          title: windowId,
          show: true,
          backgroundColor: '#eef4fc',
          webPreferences: {
            partition: `persist:${windowId}`, // 每账号独立持久分区（cookie/存储隔离+内建持久化）
            backgroundThrottling: false,      // 隐藏窗口不节流（自动登录时序保障）
            contextIsolation: true,
            nodeIntegration: false,
          },
        });
        win.loadURL(serverUrl(`/static/blank.html?w=${encodeURIComponent(windowId)}`));
        sessionWindows.set(windowId, win);
        win.on('closed', () => sessionWindows.delete(windowId));
        return json(res, 200, { ok: true });
      }

      if (req.method === 'POST' && url.pathname === '/windows/focus') {
        const win = sessionWindows.get(String(payload.windowId || ''));
        if (!win || win.isDestroyed()) return json(res, 404, { error: 'no such window' });
        if (win.isMinimized()) win.restore();
        win.show();
        win.focus();
        return json(res, 200, { ok: true });
      }

      if (req.method === 'POST' && url.pathname === '/windows/close') {
        const win = sessionWindows.get(String(payload.windowId || ''));
        if (win && !win.isDestroyed()) win.close();
        return json(res, 200, { ok: true });
      }

      json(res, 404, { error: 'not found' });
    });
  });
  server.listen(CONTROL_PORT, '127.0.0.1');
}

// ------------------------------------------------------------ 托盘

function createTray() {
  try {
    tray = new Tray(path.join(__dirname, 'icon.ico'));
  } catch {
    return; // 图标缺失不阻塞
  }
  const menu = Menu.buildFromTemplate([
    { label: '显示主窗', click: () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } } },
    { label: '账号轮盘 (Alt+Q)', click: toggleWheel },
    { type: 'separator' },
    { label: '退出', click: () => app.quit() },
  ]);
  tray.setToolTip('Akso Workbench');
  tray.setContextMenu(menu);
  tray.on('double-click', () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } });
}

// ------------------------------------------------------------ 启动流程

app.whenReady().then(async () => {
  spawnServer();
  createControlServer();
  createTray();

  globalShortcut.register('Alt+Q', toggleWheel);

  const ready = await waitReady();
  createMain();
  if (ready) {
    mainWindow.show();
  } else {
    mainWindow.loadURL(serverUrl('/static/blank.html?error=timeout'));
    mainWindow.show();
  }

  // 自动更新（打包态；未发布版本时静默失败）
  if (app.isPackaged) {
    try {
      const { autoUpdater } = require('electron-updater');
      autoUpdater.autoDownload = true;
      autoUpdater.checkForUpdatesAndNotify().catch(() => {});
    } catch { /* updater 未配置时忽略 */ }
  }
});

app.on('window-all-closed', () => {
  app.quit();
});

app.on('before-quit', () => {
  if (quitting) return;
  quitting = true;
  globalShortcut.unregisterAll();
  killServer();
});
