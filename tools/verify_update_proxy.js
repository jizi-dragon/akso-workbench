// 验证「出网通道自动选择」是否真的让更新下载走得通（真跑 Electron 网络栈，不是模拟）
//
// 背景（实测）：electron-updater 的链路是
//   github.com/<o>/<r>/releases.atom（查版本）→ github.com/…/releases/download/…（302）
//   → release-assets.githubusercontent.com（CDN）
// 这台机器**直连 github.com 是间歇性的**：实测同一 URL 1.1s 成功与 20s 超时都出现过；
// 而代理稳定但慢 —— 所以默认应该走直连，只有直连真不通才回落代理。
//
// 本脚本做两件事：
//   1) 不设任何通道，用 net.fetch 拉一次真实 Release 资产 URL（基线，仅参考）；
//   2) 调 proxy.js 的自动策略（直连探测 → 失败才回落系统代理），再拉同一个 URL。
// 判定：**策略生效后必须能拿到 latest.yml**。直连基线成功不算失败——那正是 auto 策略
// 想要的结果（能直连就不配代理）。
//
// 运行：electron.exe tools/verify_update_proxy.js

const { app, net, session } = require('electron');
const ASSET =
  'https://github.com/jizi-dragon/akso-mogul/releases/latest/download/latest.yml';

function log(o) {
  process.stdout.write(`AKSO_PROBE ${JSON.stringify(o)}\n`);
}

async function tryFetch(label, timeoutMs) {
  const t0 = Date.now();
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const resp = await net.fetch(ASSET, { signal: ctl.signal });
    const body = await resp.text();
    const ok = resp.status === 200 && /version:\s*\d/.test(body);
    log({ label, ok, status: resp.status, ms: Date.now() - t0, version: (body.match(/version:\s*(\S+)/) || [])[1] || null });
    return ok;
  } catch (e) {
    log({ label, ok: false, error: String((e && e.message) || e).slice(0, 120), ms: Date.now() - t0 });
    return false;
  } finally {
    clearTimeout(timer);
  }
}

app.whenReady().then(async () => {
  const os = require('os');
  const path = require('path');
  const proxy = require('../desktop/proxy.js');

  // 1) 直连能不能用（Chromium 默认行为）
  const before = await tryFetch('no-proxy', 20000);
  // 2) 按 proxy.js 的自动策略选通道：优先生成直连，失败才回落系统代理
  const info = await proxy.apply(session.defaultSession, {
    dataDir: path.join(os.tmpdir(), 'akso-proxy-probe-none'),
    net,
    log: () => {},
  });
  log({ appliedProxy: info });
  const after = await tryFetch('after-apply', 30000);

  // 判定标准：**应用策略后必须能拿到 latest.yml** —— 这是更新下载链的第一跳，
  // 它不通后面 350MB 的安装包更不可能下来。
  // 注意 `before` 只作参考：本机直连 github.com 是**间歇性**的（实测同一 URL
  // 1.1s 成功与 20s 超时都出现过），所以"直连也成功"不是失败信号——
  // 那正是 auto 策略想要的结果：能直连就不碰代理。
  log({
    conclusion: {
      strategyOk: after,
      directOk: before,
      chosen: info.server ? `代理 ${info.server}` : '直连',
      source: info.source,
      probe: info.probe,
      verdict: after
        ? info.server
          ? 'PASS（直连不稳，已自动回落到代理且下载通道可用）'
          : 'PASS（直连可用 → 不配代理）'
        : 'FAIL（两种通道都拿不到 latest.yml：检查网络/代理端口）',
    },
  });
  app.exit(after ? 0 : 1);
});
