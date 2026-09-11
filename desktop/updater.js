// 桌面壳自动更新（用户定稿：传输通道 = GitHub Releases + electron-updater；
// 触发策略 = 静默后台下载 + 退出时安装 + 手动检查 + 启动时检查）
//
// 设计要点（为什么这么写）：
// 1) 只在打包态启用：开发态（electron . 直跑）没有 app-update.yml，检查必然抛错；
// 2) 静默下载不打断工作：下载完成不弹窗、不抢焦点，只改托盘提示与账号中心版本角标；
//    真正安装（会关掉当前应用）交给「用户自己退出时」——即 autoInstallOnAppQuit；
// 3) 退出时安装必须先断 sidecar：安装器要覆盖 AksoServer.exe / 扩展目录，
//    服务进程还活着会锁文件（main.js 的 drain 回调 → updater.installOnExit）；
// 4) 手工检查的结论必须落地（弹窗），自动检查失败则完全静默——后台噪音是负价值。
//
// 本模块不依赖 Electron 之外的东西，state() 是纯数据，便于 verify_updater_logic.mjs
// 用桩对象在纯 node 下跑状态机。

const path = require('path');
const { app, dialog } = require('electron');

/** 自动检查节拍：启动后 20s 首查（不与启动 I/O 抢带宽），之后每 6h 一次 */
const FIRST_CHECK_DELAY_MS = 20_000;
const PERIODIC_CHECK_MS = 6 * 60 * 60 * 1000;
/** 状态心跳：Python 侧凭 shell-state.json 的新鲜度判断壳是否还在（TTL 60s）。
 *  没有心跳，壳静止 60s 后账号中心就会把版本角标降级成「桌面壳未运行」。 */
const HEARTBEAT_MS = 15_000;
/** 事件去抖：一次检查会连发 checking → update-available → …，不必逐条入日志 */
const EVENT_DEBOUNCE_MS = 1200;

let autoUpdater = null;
let initialized = false;
let wired = false;
let checkPromise = null;
let lastCheckAt = 0;
let listeners = [];

/** 更新状态机（唯一真源；托盘/版本角标/接口都读它） */
const S = {
  supported: false,   // 本进程能否更新（打包态才有 app-update.yml）
  phase: 'idle',      // idle | checking | latest | downloading | ready | error
  currentVersion: '',
  availableVersion: '',
  percent: 0,
  message: '',
  lastCheckAt: 0,
  lastError: '',
  manual: false,
};

const PHASE_LABEL = {
  idle: '未检查更新',
  checking: '正在检查更新…',
  latest: '已是最新版本',
  downloading: '正在后台下载更新…',
  ready: '更新已就绪，退出时安装',
  error: '检查更新失败',
};

function emit() {
  const snapshot = state();
  for (const fn of listeners) {
    try {
      fn(snapshot);
    } catch {
      // 监听者异常不得影响更新流程
    }
  }
}

/** 状态快照（纯数据，可 JSON 化：写 shell-state.json / 供 UI 读取） */
function state() {
  const busy = S.phase === 'checking' || S.phase === 'downloading';
  return {
    supported: S.supported,
    phase: S.phase,
    label: PHASE_LABEL[S.phase] || S.phase,
    currentVersion: S.currentVersion,
    availableVersion: S.availableVersion,
    percent: S.percent,
    message: S.message,
    lastCheckAt: S.lastCheckAt,
    lastError: S.lastError,
    updatePending: S.phase === 'ready' || S.phase === 'downloading',
    busy,
  };
}

/** 安装闸：退出路径（before-quit）与「立即重启安装」可能同时到达，
 *  quitAndInstall 只能拉一次（第二次调用会因为文件锁/进程已在退出而失败告警）。 */
let installTriggered = false;

function onChange(fn) {
  listeners.push(fn);
  return () => {
    listeners = listeners.filter((f) => f !== fn);
  };
}

/** 托盘角标文案（无更新时为 null，避免托盘里塞无用项） */
function trayHint() {
  if (S.phase === 'downloading') return `正在下载更新 v${S.availableVersion || ''} ${S.percent || 0}%`;
  if (S.phase === 'ready') return `更新 v${S.availableVersion} 已就绪 · 退出时安装`;
  return null;
}

function wire() {
  if (wired) return;
  wired = true;

  autoUpdater.autoDownload = true;          // 静默后台下载
  autoUpdater.autoInstallOnAppQuit = true;  // 退出时安装（真正的安装动作由 main.js drain 触发）
  autoUpdater.allowPrerelease = false;
  autoUpdater.logger = null;                // 不额外引 electron-log

  // 离线自测通道：AKSO_UPDATE_OVERRIDE=<url> 指向一个静态目录（内含 latest.yml 与安装包），
  // 用 generic provider 代替 GitHub。为什么需要：验证"检查→下载→就绪→退出时安装"整条链
  // 若必须真的拉一次 350MB 安装包，成本高到没人会做；有了它，本地起个静态服务就能在几十秒内
  // 跑完整条链（tools/verify_update_flow.py 即基于此）。**只影响更新源，不改任何判定逻辑**。
  const override = (process.env.AKSO_UPDATE_OVERRIDE || '').trim();
  if (override) {
    try {
      autoUpdater.setFeedURL({ provider: 'generic', url: override });
    } catch (e) {
      S.lastError = `覆盖更新源失败：${e && e.message}`;
    }
  }

  autoUpdater.on('checking-for-update', () => {
    S.phase = 'checking';
    S.message = '';
    emit();
  });

  autoUpdater.on('update-available', (info) => {
    S.availableVersion = String(info?.version || '');
    S.phase = 'downloading';
    S.percent = 0;
    S.message = `发现新版本 v${S.availableVersion}，正在后台下载`;
    emit();
  });

  autoUpdater.on('download-progress', (p) => {
    S.percent = Number(p?.percent || 0);
    emit();
  });

  autoUpdater.on('update-not-available', () => {
    S.availableVersion = '';
    S.percent = 0;
    S.phase = 'latest';
    S.message = '当前已是最新版本';
    emit();
  });

  autoUpdater.on('update-downloaded', (info) => {
    S.availableVersion = String(info?.version || S.availableVersion || '');
    S.phase = 'ready';
    S.percent = 100;
    S.message = `v${S.availableVersion} 已下载完成，退出应用时自动安装`;
    emit();
    // 只提示一次（去抖）：不打断操作，用户点「立即」才重启安装
    setTimeout(() => {
      if (S.phase !== 'ready' || S.manual) return;
      void dialog
        .showMessageBox({
          type: 'info',
          title: '更新已就绪',
          message: `Akso Workbench v${S.availableVersion} 已下载完成`,
          detail: '将在你退出应用时自动安装（无需再管）。\n也可以现在就重启完成安装。',
          buttons: ['退出时安装（推荐）', '立即重启安装'],
          defaultId: 0,
          cancelId: 0,
          noLink: true,
        })
        .then((r) => {
          if (r?.response === 1) installOnExit();
        })
        .catch(() => undefined);
    }, EVENT_DEBOUNCE_MS);
  });

  autoUpdater.on('error', (err) => {
    const msg = String(err?.message || err || '未知错误');
    S.lastError = msg;
    // 手工检查的结论要落地（由 showResult 弹窗）；自动检查失败保持完全静默
    S.phase = S.manual ? 'error' : 'idle';
    S.message = msg;
    emit();
  });
}

/**
 * 检查更新。manual=true 时返回结构化结论供弹窗；自动检查吞掉一切异常。
 * 同一时刻只允许一次检查（并发检查会互相覆盖 phase）。
 */
async function check(manual = false) {
  if (!S.supported) {
    return {
      ok: false,
      phase: 'unsupported',
      message: app.isPackaged ? '更新组件不可用' : '开发态（未打包）不支持自动更新',
    };
  }
  // 已下载完的更新：再检查一次没有意义，直接把球踢给安装路径
  if (S.phase === 'ready') {
    return { ok: true, phase: 'ready', version: S.availableVersion, message: S.message };
  }
  if (checkPromise) return checkPromise;

  S.manual = manual;
  S.lastError = '';
  checkPromise = (async () => {
    try {
      const result = await autoUpdater.checkForUpdates();
      // 下载由 autoDownload 在后台推进；此处只回报「有没有新版本」这个事实
      const found = String(result?.updateInfo?.version || '');
      if (found && found !== app.getVersion()) {
        S.availableVersion = found;
        if (S.phase !== 'ready') {
          S.phase = 'downloading';
          S.message = `发现新版本 v${found}，正在后台下载`;
        }
        emit();
        return { ok: true, phase: 'downloading', version: found, message: S.message };
      }
      return { ok: true, phase: 'latest', version: app.getVersion(), message: '当前已是最新版本' };
    } catch (e) {
      const msg = String(e?.message || e);
      S.lastError = msg;
      emit();
      return { ok: false, phase: 'error', message: msg };
    } finally {
      S.lastCheckAt = Date.now();
      checkPromise = null;
      emit();
    }
  })();
  return checkPromise;
}

/** 手工检查（托盘菜单 / 账号中心版本角标）：结论必须反馈给用户 */
async function checkAndReport() {
  const r = await check(true);
  if (!r.ok) {
    const detail =
      r.phase === 'unsupported'
        ? '自动更新仅在安装版（.exe 安装后运行）中生效；开发态请用 git pull 更新。'
        : `${r.message}\n\n常见原因：网络无法访问 GitHub Releases、公司网络拦截、或尚未发布新版本。`;
    await dialog.showMessageBox({
      type: 'warning',
      title: '检查更新',
      message: '未能完成更新检查',
      detail,
      buttons: ['知道了'],
      noLink: true,
    });
    return r;
  }
  if (r.phase === 'latest') {
    await dialog.showMessageBox({
      type: 'info',
      title: '检查更新',
      message: '当前已是最新版本',
      detail: `Akso Workbench v${app.getVersion()}`,
      buttons: ['好'],
      noLink: true,
    });
    return r;
  }
  if (r.phase === 'ready') {
    const pick = await dialog.showMessageBox({
      type: 'info',
      title: '检查更新',
      message: `v${r.version} 已下载完成，等待安装`,
      detail: '退出应用时自动安装。也可以现在就重启完成安装。',
      buttons: ['退出时安装', '立即重启安装'],
      defaultId: 0,
      cancelId: 0,
      noLink: true,
    });
    if (pick.response === 1) installOnExit();
    return r;
  }
  await dialog.showMessageBox({
    type: 'info',
    title: '检查更新',
    message: `发现新版本 v${r.version}`,
    detail: '正在后台下载；下载完成后不会打断你，退出应用时自动安装。',
    buttons: ['好'],
    noLink: true,
  });
  return r;
}

/**
 * 触发「退出时安装」：调用方（main.js）必须已经停掉 sidecar —— 安装器要覆盖
 * AksoServer.exe 与扩展目录，服务进程持有文件句柄时覆盖会失败。
 */
function installOnExit() {
  if (S.phase !== 'ready' || !autoUpdater || installTriggered) return false;
  installTriggered = true;
  try {
    autoUpdater.quitAndInstall(false, true);
    return true;
  } catch {
    // 安装器拉起失败：让应用照常退出，下次开机 updater 会重新发现已下载的更新
    return false;
  }
}

function init({ onState } = {}) {
  if (onState) onChange(onState);
  S.currentVersion = app.getVersion();
  S.lastCheckAt = lastCheckAt;
  if (!app.isPackaged) {
    S.supported = false;
    emit();
    return state();
  }
  try {
    ({ autoUpdater } = require('electron-updater'));
  } catch {
    S.supported = false;
    emit();
    return state();
  }
  S.supported = true;
  wire();
  emit();
  return state();
}

/** 启动检查 + 定时检查（静默：失败不打扰用户）+ 状态心跳 */
function start() {
  // 心跳不分打包态：开发态也要让 UI 知道「壳在跑，只是不支持自动更新」
  setInterval(emit, HEARTBEAT_MS);
  if (!S.supported) return;
  setTimeout(() => void check(false), FIRST_CHECK_DELAY_MS);
  setInterval(() => void check(false), PERIODIC_CHECK_MS);
}

module.exports = {
  init,
  start,
  check,
  checkAndReport,
  installOnExit,
  onChange,
  trayHint,
  state,
  PHASE_LABEL,
};
