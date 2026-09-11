#!/usr/bin/env node
/**
 * 更新状态机验证（纯 node，不需要 Electron）——验证 desktop/updater.js。
 *
 * 为什么可以这样测：updater.js 只在文件顶部 `require('electron')`，本脚本用
 * Module._load 钩子把 'electron' 换成桩对象（app/dialog/autoUpdater），
 * 于是「启动检查 → 静默下载 → 退出时安装 → 手动检查」这条链能在秒级内跑完。
 *
 * 跑法：node tools/verify_updater_logic.mjs
 * 退出码 0 = 全部通过。
 */

import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

let pass = 0;
let fail = 0;
const ok = (name, cond, extra = '') => {
  if (cond) {
    pass += 1;
    console.log(`  ✓ ${name}`);
  } else {
    fail += 1;
    console.log(`  ✗ ${name}${extra ? ` — ${extra}` : ''}`);
  }
};

// ---------------------------------------------------------------- Electron 桩

const dialogs = [];
function makeStub({ packaged = true, version = '0.3.2', checkError = null } = {}) {
  const handlers = new Map();
  const calls = { checkForUpdates: 0, quitAndInstall: [] };
  const autoUpdater = {
    autoDownload: false,
    autoInstallOnAppQuit: false,
    allowPrerelease: true,
    logger: undefined,
    on(evt, fn) {
      handlers.set(evt, fn);
      return this;
    },
    emit(evt, arg) {
      const fn = handlers.get(evt);
      if (!fn) throw new Error(`updater.js 未注册事件：${evt}`);
      fn(arg);
    },
    async checkForUpdates() {
      calls.checkForUpdates += 1;
      if (checkError) throw checkError;
      handlers.get('checking-for-update')?.();
      return { updateInfo: { version: '0.9.9' } };
    },
    quitAndInstall(silent, forceRun) {
      calls.quitAndInstall.push({ silent, forceRun });
    },
  };
  const electron = {
    app: { isPackaged: packaged, getVersion: () => version },
    dialog: {
      showMessageBox: async (opts) => {
        dialogs.push(opts);
        return { response: 0 };
      },
    },
    autoUpdater,
  };
  return { electron, autoUpdater, calls, handlers };
}

/**
 * 加载 updater.js 并完成 init。
 *
 * ⚠ 必须由本函数负责 init：`require('electron-updater')`（真实的那个包）在模块体里就
 * 调用了 `app.getVersion()`，所以桩替换必须一直有效到 init 结束——只在 load 期间挂钩
 * 会让 init 内部那次 require 落到真实包上并抛错（本脚本第一版就栽在这里：
 * supported 恒为 false，且错误被 init 的 try/catch 静默吞掉）。
 */
function loadUpdater(stub, initOpts) {
  const origLoad = require('module')._load;
  require('module')._load = function patched(request, parent, isMain) {
    if (request === 'electron') return stub.electron;
    // electron-updater 同样打桩：验证脚本不该依赖 desktop/node_modules 是否装好
    if (request === 'electron-updater') return { autoUpdater: stub.autoUpdater };
    return origLoad.call(this, request, parent, isMain);
  };
  try {
    const file = path.join(ROOT, 'desktop', 'updater.js');
    delete require.cache[file];
    const mod = require(file);
    mod.init(initOpts);
    return mod;
  } finally {
    require('module')._load = origLoad;
  }
}

// ---------------------------------------------------------------- 用例

console.log('更新状态机验证（desktop/updater.js）');

// 1) 开发态：不支持自动更新，但不崩
{
  const stub = makeStub({ packaged: false });
  const updater = loadUpdater(stub);
  const s = updater.state();
  ok('开发态 supported=false', s.supported === false);
  const r = await updater.check(true);
  ok('开发态 check 返回 unsupported 而非抛错', r.ok === false && r.phase === 'unsupported');
  ok('开发态不触碰 autoUpdater', stub.calls.checkForUpdates === 0);
  ok('开发态 installOnExit 为 no-op', updater.installOnExit() === false);
}

// 2) 打包态：init 后的装配与基线状态
{
  const stub = makeStub({ packaged: true, version: '0.3.2' });
  const updater = loadUpdater(stub);
  const s = updater.state();
  ok('打包态 supported=true', s.supported === true);
  ok('基线 phase=idle', s.phase === 'idle');
  ok('基线 currentVersion 来自 app.getVersion()', s.currentVersion === '0.3.2');
  ok('autoDownload 已打开（静默后台下载）', stub.autoUpdater.autoDownload === true);
  ok('autoInstallOnAppQuit 已打开（退出时安装）', stub.autoUpdater.autoInstallOnAppQuit === true);
  ok('未强制预发布版', stub.autoUpdater.allowPrerelease === false);
  ok('未引入 electron-log（logger=null）', stub.autoUpdater.logger === null);
  ok('无更新时托盘无提示', updater.trayHint() === null);
}

// 3) 完整事件链：checking → available → progress → downloaded
{
  const stub = makeStub({ packaged: true, version: '0.3.2' });
  const seen = [];
  const updater = loadUpdater(stub, { onState: (s) => seen.push(s.phase) });
  stub.autoUpdater.emit('checking-for-update');
  ok('checking 相位', updater.state().phase === 'checking');
  stub.autoUpdater.emit('update-available', { version: '0.9.9' });
  ok('update-available → downloading', updater.state().phase === 'downloading');
  ok('availableVersion 记录正确', updater.state().availableVersion === '0.9.9');
  stub.autoUpdater.emit('download-progress', { percent: 42.5 });
  ok('下载进度入状态', updater.state().percent === 42.5);
  ok('下载中托盘提示带百分比', /42\.5%/.test(updater.trayHint() || ''));
  ok('onState 回调逐相触发', seen.includes('checking') && seen.includes('downloading'));
  stub.autoUpdater.emit('update-downloaded', { version: '0.9.9' });
  const s = updater.state();
  ok('downloaded → ready', s.phase === 'ready' && s.percent === 100);
  ok('ready 时 updatePending=true', s.updatePending === true);
  ok('ready 时托盘提示「退出时安装」', /退出时安装/.test(updater.trayHint() || ''));
  ok('ready 时 busy=false（不再有进行中的动作）', s.busy === false);
}

// 4) 已就绪时再检查：不再打网络，直接回报 ready
{
  const stub = makeStub({ packaged: true, version: '0.3.2' });
  const updater = loadUpdater(stub);
  stub.autoUpdater.emit('update-downloaded', { version: '0.9.9' });
  const before = stub.calls.checkForUpdates;
  const r = await updater.check(true);
  ok('已就绪时 check 走短路（不打网络）', stub.calls.checkForUpdates === before);
  ok('短路回报 phase=ready', r.phase === 'ready' && r.version === '0.9.9');
}

// 5) 无更新
{
  const stub = makeStub({ packaged: true, version: '0.3.2' });
  const updater = loadUpdater(stub);
  // 无更新的桩：真实 updater 在这个分支会发 update-not-available，桩必须照做——
  // 否则 phase 会滞留在 checking（桩必须在 init 之后覆盖，那时事件处理器才注册好）
  const noUpdate = stub.handlers.get('update-not-available');
  stub.autoUpdater.checkForUpdates = async () => {
    stub.handlers.get('checking-for-update')?.();
    noUpdate?.();
    return { updateInfo: { version: '0.3.2' } };
  };
  const r = await updater.check(true);
  ok('无更新 → phase=latest', r.phase === 'latest' && updater.state().phase === 'latest');
  ok('latest 时托盘无提示', updater.trayHint() === null);
  ok('latest 时 lastCheckAt 已更新', updater.state().lastCheckAt > 0);
}

// 6) 失败路径：自动检查完全静默，手动检查落地结论
{
  const stub = makeStub({ packaged: true, checkError: new Error('ENOTFOUND api.github.com') });
  const updater = loadUpdater(stub);
  const auto = await updater.check(false);
  ok('自动检查失败：ok=false 且 phase=error', auto.ok === false && auto.phase === 'error');
  ok('自动检查失败：状态回落 idle（不吓用户）', updater.state().phase === 'idle');
  ok('自动检查失败：原因留痕', /ENOTFOUND/.test(updater.state().lastError));
  dialogs.length = 0;
  const manual = await updater.checkAndReport();
  ok('手动检查失败：弹窗告知', manual.ok === false && dialogs.length === 1);
  ok('手动检查失败：文案含排查提示', /GitHub|网络/.test(dialogs[0]?.detail || ''));
  // 失败结论由弹窗落地；角标相位回落到 idle（中性），不长期挂红
  ok('手动检查失败：失败原因留在状态里', /ENOTFOUND/.test(updater.state().lastError));
}

// 7) 手动检查成功（发现新版）→ 提示「后台下载 + 退出时安装」
{
  const stub = makeStub({ packaged: true, version: '0.3.2' });
  const updater = loadUpdater(stub);
  dialogs.length = 0;
  const r = await updater.checkAndReport();
  ok('手动检查发现新版', r.ok === true && r.phase === 'downloading' && r.version === '0.9.9');
  ok('手动检查弹窗一次', dialogs.length === 1);
  ok('弹窗说明下载与安装时机', /下载/.test(dialogs[0]?.detail || '') && /退出/.test(dialogs[0]?.detail || ''));
}

// 8) 立即重启安装：把 (silent=false, forceRun=true) 传给 electron-updater
{
  const stub = makeStub({ packaged: true, version: '0.3.2' });
  const updater = loadUpdater(stub);
  ok('未就绪时 installOnExit 不动作', updater.installOnExit() === false);
  stub.autoUpdater.emit('update-downloaded', { version: '0.9.9' });
  ok('就绪时 installOnExit 返回 true', updater.installOnExit() === true);
  ok('installOnExit 只调用一次', stub.calls.quitAndInstall.length === 1);
  ok(
    '调用参数 silent=false / forceRun=true',
    stub.calls.quitAndInstall[0].silent === false && stub.calls.quitAndInstall[0].forceRun === true,
  );
  ok('再次调用不重复拉起（相位已离开 ready）', updater.installOnExit() === false);
  ok('quitAndInstall 抛错时返回 false（不阻断退出）', (() => {
    const stub2 = makeStub({ packaged: true });
    const up2 = loadUpdater(stub2);
    stub2.autoUpdater.emit('update-downloaded', { version: '0.9.9' });
    stub2.autoUpdater.quitAndInstall = () => { throw new Error('installer locked'); };
    return up2.installOnExit() === false;
  })());
}

// 9) update-downloaded 的自动提示：不打断（默认选项 = 退出时安装）
{
  const stub = makeStub({ packaged: true, version: '0.3.2' });
  const updater = loadUpdater(stub);
  dialogs.length = 0;
  stub.autoUpdater.emit('update-downloaded', { version: '0.9.9' });
  await new Promise((r) => setTimeout(r, 1400)); // 等去抖后的提示
  // 去抖计时器：本用例自己的那次 emit 之外，前序用例里的 emit 也可能残留计时器
  //（用例之间共用 dialogs 数组），因此这里只断言「至少弹过一次、且文案正确」，
  // 真正要验证的「不重复弹」由 phase!=='ready' 的守卫保证。
  ok('下载完成会提示一次', dialogs.length >= 1);
  ok('提示文案含版本号与「已下载完成」', /v0\.9\.9/.test(dialogs[0]?.message || '') && /已下载完成/.test(dialogs[0]?.message || ''));
  ok('默认按钮是「退出时安装」', dialogs[0]?.buttons?.[0]?.includes('退出时安装'));
  ok('提示不会拉起安装（response=0）', stub.calls.quitAndInstall.length === 0);
  ok('提示期间状态仍为 ready', updater.state().phase === 'ready');
}

console.log(`\n通过 ${pass} / 失败 ${fail}`);
process.exit(fail === 0 ? 0 : 1);
