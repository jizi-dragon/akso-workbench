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
const portBroker = require('./ports');
const proxy = require('./proxy');

// 端口**固定**（ADR-0012 取代 ADR-0011 §D-1）：18765 服务端 / 18766 CDP / 18767 壳控制服务。
// 不做顺延——被占就报错退出，并说清是谁占的（我们自己的另一个实例，还是无关程序）。
// 判定"谁占着"必须核对身份：只看"端口有人应答"会认错服务（PITFALLS #20），
// 只看"pid 还活着"会被 pid 复用误报（#25）。
const SERVER_PORT = portBroker.PORTS.server;
const CONTROL_PORT = portBroker.PORTS.control;
const DEBUG_PORT = portBroker.PORTS.cdp;

let serverChild = null;
let mainWindow = null;
let wheelWindow = null;
let tray = null;
let quitting = false;
/** 端口是固定的；这里只留壳的启动时刻，供 /whoami 与排障使用 */
let portInfo = { ports: { ...portBroker.PORTS }, startedAt: null };
/** updater 状态最后一次快照：供控制服务 /update-state 与渲染层 IPC 读取 */
let updateState = null;
/** 代理解析结果（写入壳状态，便于排障："更新下载不动"时先看这里） */
let proxyInfo = { server: null, source: 'pending' };

// 会话窗注册表：windowId → BrowserWindow（browser_pool CDP 模式经控制服务开户窗）
const sessionWindows = new Map();

const serverUrl = (p) => `http://127.0.0.1:${SERVER_PORT}${p}`;

// —— CDP 调试端口：必须在 app ready **之前**设置 ——
// 端口是固定常量，故在模块加载期就能设定，不需要再等"协商完成"这个异步点。
function applyCdpSwitch() {
  app.commandLine.appendSwitch('remote-debugging-port', String(DEBUG_PORT));
  app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1');
}

// ------------------------------------------------------------ 服务 sidecar

function spawnServer() {
  // 端口下发的两条路一起走（ADR-0011 §D-2，端口值本身已按 ADR-0012 固定）：
  //  - 环境变量：Python 侧 config.PORT/CDP_PORT/CONTROL_PORT 是唯一读数点；
  //  - 命令行 --port：打包态入口 server_entry.main() 已支持解析，两者必须一致。
  // `WORKBENCH_SESSION_MODE=cdp` 也在这里下发——它此前**从没被壳设置过**，导致
  // browser_pool 永远跑 local 模式（托管会话用的是原生 Chromium 而不是壳的 Chromium）。
  const env = {
    ...process.env,
    WORKBENCH_PORT: String(SERVER_PORT),
    WORKBENCH_CDP_PORT: String(DEBUG_PORT),
    WORKBENCH_CONTROL_PORT: String(CONTROL_PORT),
    WORKBENCH_SESSION_MODE: 'cdp',
  };
  const packaged = process.resourcesPath
    ? path.join(process.resourcesPath, 'server', 'AksoServer.exe')
    : null;
  if (packaged && fs.existsSync(packaged)) {
    serverChild = spawn(packaged, ['--server', '--port', String(SERVER_PORT)], {
      cwd: path.dirname(packaged),
      env,
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
      { cwd: repoRoot, env, stdio: 'ignore', windowsHide: true });
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

/**
 * 等服务端就绪——**并且核对身份**。
 *
 * 改造前这里是 `http.get('/')` 只看 `statusCode === 200`，判据是"这个端口上有人应答"。
 * 于是一个**旧实例**（或任何别的程序）占着 18765 时，新壳会认为自己的服务已经起来了，
 * 主窗打开的是别人的页面——那次事故表现成"代码改了但 UI 没变"（ADR-0011 §1）。
 *
 * 现在改成打 `GET /api/health` 并核对 `app === 'AksoWorkbench'`：
 * 从"有人应答"升级为"是我们的服务在应答"。探测端点必须**无副作用**——
 * 别用 `/extension/health`，它会写 `everConnected` 闩锁（ADR-0011 §D-4）。
 *
 * @returns {Promise<{ok: boolean, reason: string, seen: object|null}>}
 */
function waitReady(timeoutMs = 40000) {
  return new Promise((resolve) => {
    const deadline = Date.now() + timeoutMs;
    let lastSeen = null;
    let lastReason = 'no-response';
    let settled = false;

    const finish = (ok, reason) => {
      if (settled) return;
      settled = true;
      resolve({ ok, reason, seen: lastSeen });
    };

    const probeOnce = () => new Promise((done) => {
      const req = http.get(`http://127.0.0.1:${SERVER_PORT}/api/health`, { timeout: 2000 }, (res) => {
        let body = '';
        res.setEncoding('utf8');
        res.on('data', (c) => { body += c; });
        res.on('end', () => {
          let data = null;
          try { data = JSON.parse(body); } catch { data = null; }
          if (data && data.app === 'AksoWorkbench') {
            lastSeen = data;
            lastReason = 'ok';
          } else {
            lastReason = data ? `wrong-app:${data.app || 'unknown'}` : 'non-json';
          }
          done();
        });
      });
      req.on('error', () => { lastReason = 'no-response'; done(); });
      req.on('timeout', () => { lastReason = 'timeout'; req.destroy(); });
    });

    const loop = async () => {
      while (!settled) {
        // 就绪探测本就串行：一次一个请求
        await probeOnce();
        if (lastReason === 'ok') return finish(true, 'ok');
        if (Date.now() >= deadline) return finish(false, lastReason);
        await new Promise((r) => setTimeout(r, 300));
      }
    };
    void loop();
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

// ------------------------------------------------- 会话控制服务（壳内 HTTP）

/** 本壳的身份与端口——`/whoami` 与 desktop-ports.json 用**同一份**口径 */
function selfDescription() {
  return {
    app: 'AksoWorkbench',
    service: 'workbench-shell',
    controlPort: CONTROL_PORT,
    serverPort: SERVER_PORT,
    cdpPort: DEBUG_PORT,
    pid: process.pid,
    version: app.getVersion(),
    startedAt: portInfo.startedAt || null,
    ports: { ...portBroker.PORTS },
    fixed: true, // 端口固定、不顺延（ADR-0012）——扩展据此可省掉"按窗口探测"
  };
}

function createControlServer() {
  const json = (res, code, body) => {
    // 扩展从任意页面发起的发现请求会带 Origin；这几个端点无凭据、只读，故允许跨源
    res.writeHead(code, {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
    });
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

      // 身份自证（ADR-0011 §D-4/§D-5，ADR-0012 保留）：扩展靠它确认"18765 上是不是
      // 我们的服务端"。控制服务是壳自己的，所以它的回答天然比"18765 上是谁"可信。
      // 端口已固定，故本端点不再承担"发现端口"的职责，只回答**身份**。
      if (url.pathname === '/whoami') return json(res, 200, selfDescription());

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
  server.on('error', (e) => {
    // 端口已在协商阶段探过，这里的失败属于"抢跑被别人拿走了"——出声，别静默
    console.error(`[akso-shell] 控制服务监听失败（${CONTROL_PORT}）：${e && e.message}`);
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

/**
 * 启动自检：**固定端口**是否可用 + 是不是我们自己的另一个实例占着（ADR-0012）。
 *
 * 不做顺延、不做协商——被占就明确失败并说清是谁占的。理由（用户定稿）：
 * 顺延引入的活动部件（协商窗口、实际端口公布、扩展按窗口探测、书签失效）
 * 换来的收益抵不上它带来的故障面；固定端口让"打开工作台"永远是同一个地址。
 *
 * 顺序：**先清陈旧公布文件 → 再判实例 → 再探端口 → 再设 CDP 开关 → 再写公布文件**。
 */
async function bootstrapPorts() {
  // 先清陈旧公布文件：应用被强杀（taskkill /F、崩溃）时 will-quit 不触发，
  // 文件会残留；若不核对写它的进程是否**真是我们的另一个实例**，下次启动就会误报
  // 「已有实例在运行」并把用户劝退（实测踩到过，见 docs/PITFALLS.md #25）。
  if (portBroker.clearStale()) {
    console.warn('[akso-shell] 已清理陈旧的 desktop-ports.json（写它的实例已不在）');
  }

  // 是不是我们自己的另一个实例在跑？（核对身份，不只看端口有没有人应答）
  const prev = portBroker.detectRunningInstance();

  // 固定端口不可用 → 说清是哪个端口、被谁占、怎么办，然后退出（不换端口）
  const busy = await portBroker.unavailable();
  if (busy.length) {
    // 只有 server 端口上"我们的另一个实例"是可识别的（公布文件记的就是它的 server）；
    // 其余端口如实说"不是我们的实例占着"，并给出找占用者的命令。
    const detail = busy
      .map((b) => portBroker.describeOccupant(b.key, b.key === 'server' ? prev : null))
      .join('\n\n');
    dialog.showMessageBoxSync({
      type: 'error',
      title: '端口被占用',
      message: `固定端口被占用，无法启动（${busy.map((b) => b.port).join('、')}）。`,
      detail: detail +
        '\n\n本次不会自动换端口——固定端口是刻意的设计（扩展地址、书签都指向它们）。',
      buttons: ['退出'],
    });
    app.exit(1);
    return false;
  }
  // 端口本身可用，且没有我们的实例在跑 → 正常启动。
  //
  // ⚠ ADR-0012 起这里**不再有**「仍然启动 / 退出」的询问：端口固定意味着
  // "活着的实例必然占着 18765"，所以那种情况已经被上面的端口检查拦住了，
  // 询问分支是**永远走不到的死代码**（留着只会让下次改这里的人以为两个实例可以并存）。
  // 顺带得到一个刻意的好处：**同一时刻只可能有一个实例**——不再有"两个实例共用同一份
  // 数据目录、各自一组端口、凭据互相解密"那一族问题（PITFALLS #23）。
  portInfo = { ports: { ...portBroker.PORTS }, startedAt: new Date().toISOString() };
  applyCdpSwitch();
  portBroker.publish(app.getVersion());
  return true;
}

app.whenReady().then(async () => {
  if (!(await bootstrapPorts())) return;
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
  if (ready.ok) {
    mainWindow.show();
  } else {
    // 把「我探到了什么」一并带进错误页：光说 timeout 无法区分
    // "服务还没起来" 和 "端口上坐着别人的服务"（后者正是 ADR-0011 要治的病）
    const detail = encodeURIComponent(`${ready.reason}|${ready.seen ? JSON.stringify(ready.seen) : ''}`);
    mainWindow.loadURL(serverUrl(`/static/blank.html?error=timeout&detail=${detail}`));
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

// 端口公布的撤下放在 will-quit：`before-quit` 在 `app.exit()` 路径上**不会触发**
// （实测：启动失败分支调用 app.exit(0) 后，desktop-ports.json 留在盘上成为陈旧文件）。
// 会核对 pid，因此"新实例已写、旧实例才退出"时不会误删新实例的文件。
app.on('will-quit', () => {
  portBroker.unpublish();
});
