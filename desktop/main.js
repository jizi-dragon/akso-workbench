// Akso Workbench 桌面壳（Electron）
// 职责：Python 服务 sidecar 生命周期 + 主窗/透明轮盘窗 + 全局热键 Alt+Q
//       + 托盘 + 会话控制服务(18767，供 browser_pool CDP 模式开户窗) + 自动更新
// 架构不变量：窗口只做"壳"，业务全在 FastAPI 服务（HTTP 暴露）；
//       托管会话复用本壳的 Chromium（CDP 18766 ← playwright connect_over_cdp）。

const { app, BrowserWindow, globalShortcut, ipcMain, net, session, Tray, Menu, shell, dialog } = require('electron');
const http = require('http');
const { spawn, execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const updater = require('./updater');
const shellState = require('./shell-state');
const proxy = require('./proxy');

const SERVER_PORT = 18765;
const CONTROL_PORT = 18767;
const DEBUG_PORT = 18766;

let serverChild = null;
let mainWindow = null;
let wheelWindow = null;
let tray = null;
let quitting = false;
/** updater 状态最后一次快照：供控制服务 /update-state 与渲染层 IPC 读取 */
let updateState = null;
/** 代理解析结果（写入壳状态，便于排障："更新下载不动"时先看这里） */
let proxyInfo = { server: null, source: 'pending' };

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
  // 失焦自动关闭（上游独立小窗语义）：400ms 启动宽限防误关
  wheelWindow.once('ready-to-show', () => {
    setTimeout(() => {
      if (wheelWindow && !wheelWindow.isDestroyed()) {
        wheelWindow.on('blur', () => {
          if (wheelWindow && !wheelWindow.isDestroyed()) {
            wheelWindow.close();
            wheelWindow = null;
          }
        });
      }
    }, 400);
  });
  wheelWindow.on('closed', () => { wheelWindow = null; });
}

// ------------------------------------------------- 浏览器扩展安装助手
// 为什么必须让用户点几下：Chrome 在 Windows 上禁止非商店扩展直接安装（拖入 .crx 被拦），
// 而「策略强制安装」（ExtensionInstallForcelist）经查在 HKCU 下普遍不生效、自托管
// update_url 亦常见失败，且本仓库没有签名私钥——故采用「随包携带 + 一键引导」这条
// 零依赖、零管理员、离线可用的路径。完整取舍见 docs/EXTENSION-INSTALL.md。

/** 扩展目录：打包态 = resources/extension（extraResources 带入）；开发态 = 仓库构建产物 */
function extensionDir() {
  const packaged = process.resourcesPath ? path.join(process.resourcesPath, 'extension') : '';
  if (packaged && fs.existsSync(path.join(packaged, 'manifest.json'))) {
    return packaged;
  }
  const dev = path.join(__dirname, '..', 'extensions', 'quick-login', 'dist');
  return fs.existsSync(path.join(dev, 'manifest.json')) ? dev : null;
}

/** 定位 chrome.exe（用户级安装在 LOCALAPPDATA，机器级在 Program Files → 再兜注册表） */
function findChrome() {
  const roots = [process.env.ProgramFiles, process.env['ProgramFiles(x86)'], process.env.LOCALAPPDATA];
  for (const root of roots.filter(Boolean)) {
    const p = path.join(root, 'Google', 'Chrome', 'Application', 'chrome.exe');
    if (fs.existsSync(p)) {
      return p;
    }
  }
  try {
    const out = execSync(
      'reg query "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\chrome.exe" /ve',
      { encoding: 'utf8', windowsHide: true },
    );
    const m = /REG_SZ\s+(.+?\.exe)/i.exec(out);
    if (m && fs.existsSync(m[1].trim())) {
      return m[1].trim();
    }
  } catch {
    // 无注册表项（未安装/无权限）
  }
  return null;
}

/** 打开 chrome://extensions + 扩展目录，并给出分步说明。
 *  `mode='install'`（默认）走首次安装四步；`mode='reload'` 是桌面端升版后的
 *  一次性「重新加载」——扩展文件由安装包覆盖，Chrome 必须先重新加载才会用新版。
 *  幂等，可反复调用。 */
async function openExtensionSetup(mode = 'install') {
  const reload = mode === 'reload';
  const dir = extensionDir();
  if (dir) {
    await shell.openPath(dir).catch(() => undefined);
  }
  const chrome = findChrome();
  if (chrome) {
    try {
      spawn(chrome, ['chrome://extensions'], { detached: true, stdio: 'ignore', windowsHide: true }).unref();
    } catch {
      // 打不开就让用户手动访问
    }
  }
  const detail = reload
    ? [
        chrome
          ? '1) 已在 Chrome 打开 chrome://extensions（没弹出请手动访问）'
          : '1) 手动打开 Chrome，访问 chrome://extensions',
        '2) 在扩展列表里找到 Akso 快捷登录',
        '3) 点该扩展卡片上的「重新加载」按钮（↻）',
        '',
        '为什么需要这一步：扩展文件随桌面端一起更新，Chrome 只在重新加载后才会用上新版本。',
        '数据（账号 / 登录态）不会丢——重新加载只重启扩展本身。',
      ].join('\n')
    : [
        chrome
          ? '1) 已在 Chrome 打开 chrome://extensions（没弹出请手动访问）'
          : '1) 手动打开 Chrome，访问 chrome://extensions',
        '2) 打开右上角「开发者模式」开关',
        '3) 点「加载已解压的扩展程序」',
        '4) 在文件夹选择框里选中已为你打开的目录（选到它本身，不要进子目录）：',
        `     ${dir || '（未找到扩展目录——请重新安装桌面端）'}`,
        '',
        '装好后「在线」徽标与 Alt+Q 轮盘即可用；账号数据由桌面端自动下发，无需在扩展里另建。',
        '注意：该目录随桌面端安装目录存在，卸载桌面端后扩展会失效。',
      ].join('\n');
  await dialog.showMessageBox({
    type: 'info',
    title: reload ? '重新加载浏览器扩展（一次性）' : '安装浏览器扩展（一次性）',
    message: reload ? '扩展文件已更新：请重新加载一次' : '还差一步：把这个扩展加载进 Chrome',
    detail,
    buttons: ['知道了'],
    noLink: true,
  });
  return { dir, chrome, mode };
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

      // 安装扩展引导（供桌面 UI 的「扩展未连接」提示条调用；也可由托盘菜单直接触发）
      if (req.method === 'POST' && url.pathname === '/extension-setup') {
        void openExtensionSetup(payload.mode === 'reload' ? 'reload' : 'install')
          .then((r) => json(res, 200, { ok: true, ...r }))
          .catch((e) => json(res, 500, { ok: false, error: String(e) }));
        return;
      }

      // 更新面（账号中心版本角标 → 本壳）：state 只读快照，check 走同一条静默下载路径
      if (url.pathname === '/update-state') {
        return json(res, 200, { ok: true, shell: true, ...(updateState || updater.state()) });
      }
      if (req.method === 'POST' && url.pathname === '/update-check') {
        void updater.checkAndReport()
          .then((r) => json(res, 200, { ok: true, ...r }))
          .catch((e) => json(res, 500, { ok: false, error: String(e) }));
        return;
      }
      if (req.method === 'POST' && url.pathname === '/update-install') {
        return json(res, 200, { ok: updater.installOnExit() });
      }

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
    { label: '安装浏览器扩展…', click: () => { void openExtensionSetup(); } },
    { type: 'separator' },
    { label: '检查更新…', click: () => { void checkUpdateFromTray(); } },
    { label: `关于 v${app.getVersion()}`, click: () => {
      const s = updateState || updater.state();
      const detail = s.phase === 'ready'
        ? `新版本 v${s.availableVersion} 已下载完成，退出应用时自动安装。`
        : s.phase === 'downloading'
          ? `正在后台下载 v${s.availableVersion}（${s.percent || 0}%），退出应用时自动安装。`
          : '自动更新：启动时静默检查，后台下载，退出应用时安装。';
      void dialog.showMessageBox({
        type: 'info',
        title: '关于 Akso Workbench',
        message: `Akso Workbench v${app.getVersion()}`,
        detail,
        buttons: ['好'],
        noLink: true,
      });
    } },
    { type: 'separator' },
    { label: '退出', click: () => app.quit() },
  ]);
  tray.setToolTip('Akso Workbench');
  tray.setContextMenu(menu);
  tray.on('double-click', () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } });
}

// ------------------------------------------------------------ 自动更新
// 分工（用户定稿）：updater.js 负责状态机（启动检查/静默下载/退出时安装/手动检查），
// proxy.js 负责出网通道（Electron 默认不读系统代理，更新下载会卡在 github.com 一跳），
// 本文件负责三件壳内的事：托盘与提示、状态落盘（供账号中心版本角标）、退出前 drain。

function onUpdateState(s) {
  updateState = s;
  shellState.write({
    app: 'akso-workbench-desktop',
    version: s.currentVersion || app.getVersion(),
    proxy: proxyInfo,
    ...s,
  });
  const hint = updater.trayHint();
  if (tray) {
    tray.setToolTip(hint ? `Akso Workbench ${s.currentVersion}\n${hint}` : `Akso Workbench ${s.currentVersion}`);
  }
  // 渲染层实时刷新（账号中心版本角标），无需等 3s 轮询
  for (const win of BrowserWindow.getAllWindows()) {
    if (!win.isDestroyed()) {
      try { win.webContents.send('akso:update-state', s); } catch { /* 窗口正在销毁 */ }
    }
  }
}

function setupUpdater() {
  updater.init({ onState: onUpdateState });
  updateState = updater.state();
  // 控制服务/渲染层都是同一份数据的读取者：一次性把当前态写出去（避免首屏空窗）
  onUpdateState(updateState);
  updater.start();
}

/**
 * 托盘「检查更新…」：先**同步**记下动作（phase=checking 立即落盘），再弹结论。
 *
 * 为什么不等结论落盘：手动检查在开发态/无新版时会走弹窗分支，若用户不点按钮，
 * 弹窗会一直挂着，状态就永远停在 checking —— 排障时（以及自动化验证时）看不到结论。
 * 先落盘让「谁在什么时候查过、结果如何」在任何情况下都可查。
 */
async function checkUpdateFromTray() {
  updateState = { ...(updateState || updater.state()), phase: 'checking', label: '正在检查更新…', busy: true };
  onUpdateState(updateState);
  const r = await updater.checkAndReport();
  updateState = updater.state();
  onUpdateState(updateState);
  console.log(`[akso-shell] 手动检查更新：ok=${r.ok} phase=${r.phase} ${r.message || ''}`);
  return r;
}

// ------------------------------------------------------------ 启动流程

app.whenReady().then(async () => {
  spawnServer();
  createControlServer();
  createTray();

  globalShortcut.register('Alt+Q', toggleWheel);

  // 出网通道必须在任何联网动作（含 updater 检查/下载）之前定下来。
  // 默认**优先生成直连**（快），只有直连探测失败才回落到系统代理（稳但慢）——
  // 用户明确要求"能直连就别配代理"。探测带超时，最长拖 6s，不会挂死启动。
  try {
    proxyInfo = await proxy.apply(session.defaultSession, {
      dataDir: shellState.dataDir(),
      net,
      log: (m) => console.log(`[akso-shell] ${m}`),
    });
  } catch (e) {
    proxyInfo = { server: null, source: `fail: ${e && e.message}` };
  }

  const ready = await waitReady();
  createMain();
  if (ready) {
    mainWindow.show();
  } else {
    mainWindow.loadURL(serverUrl('/static/blank.html?error=timeout'));
    mainWindow.show();
  }

  // 自动更新（打包态生效：启动静默检查 → 后台下载 → 退出时安装）
  setupUpdater();

  ipcMain.handle('akso:update-check', () => updater.checkAndReport());
  ipcMain.handle('akso:update-state', () => updateState || updater.state());
  ipcMain.handle('akso:update-install', () => updater.installOnExit());
});

app.on('window-all-closed', () => {
  app.quit();
});

app.on('before-quit', () => {
  if (quitting) return;
  quitting = true;
  globalShortcut.unregisterAll();
  // 顺序关键：先停 sidecar（释放 AksoServer.exe / 扩展目录的文件句柄），
  // 再让 updater 拉起安装器覆盖文件；否则安装器会因文件被占用而失败。
  killServer();
  updater.installOnExit();
});
