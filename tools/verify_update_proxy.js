// 验证代理接管是否真的让「更新下载」走得通（真跑 Electron 的网络栈，不是模拟）
//
// 背景（实测）：electron-updater 的下载链是
//   api.github.com（查版本，通）→ github.com/…/releases/download/…（302，本机直连**超时**）→ CDN（通）
// 所以"能发现新版却下载不动"的根因在中间那一跳，而 Chromium 默认并不使用系统代理。
//
// 本脚本做两件事：
//   1) 不设代理：直接用 net.fetch 拉一次真实 Release 资产 URL —— 期望失败（复现问题）；
//   2) 按 proxy.js 的策略设置代理后再拉同一个 URL —— 期望成功（证明修复有效）。
// 用的是 electron.net（= electron-updater 内部走的那套栈），所以结论可直接外推。
//
// 运行（由 tools/verify_update_proxy.py 调用）：
//   electron.exe tools/verify_update_proxy.js

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

  // 不设代理先跑一次（Chromium 默认行为，用于基线对比）
  const before = await tryFetch('no-proxy', 20000);
  // dataDir 指向一个不存在的临时目录 → 不读 proxy.txt 覆盖，走到"系统代理/直连"分支
  const info = await proxy.apply(session.defaultSession, {
    dataDir: path.join(os.tmpdir(), 'akso-proxy-probe-none'),
    log: () => {},
  });
  log({ appliedProxy: info });
  const after = await tryFetch('with-proxy', 30000);

  // 判定标准：**应用代理后必须能拿到 latest.yml** —— 这是更新下载链的第一跳，
  // 它不通后面 350MB 的安装包更不可能下来。
  // 对比项 `before` 只作参考：本机直连 github.com 是**时通时不通**（实测同一 URL
  // 20s 超时与 7.3s 成功都出现过），所以不能把"直连也成功"当成失败。
  const speedup = before && after ? null : undefined;
  log({
    conclusion: {
      withProxyOk: after,
      directOk: before,
      proxy: info.server,
      source: info.source,
      verdict: after
        ? before
          ? 'PASS（代理可用；直连本次也通，属间歇性）'
          : 'PASS（直连不通、代理修复有效）'
        : 'FAIL（应用代理后仍拿不到 latest.yml：代理未运行/端口不对/被墙）',
      speedupNote: speedup,
    },
  });
  app.exit(after ? 0 : 1);
});
