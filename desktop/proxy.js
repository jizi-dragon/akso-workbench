// 网络出网通道选择（桌面壳）
//
// 背景（实测）：electron-updater 的下载链是
//   github.com/<o>/<r>/releases.atom（查版本，1.9s）
//   → github.com/…/releases/download/…（302）
//   → release-assets.githubusercontent.com（CDN，很快）
// 这台机器**直连 github.com 是间歇性的**：实测同一 URL 有时 1.1s 成功、有时 20s 超时；
// 而 Chromium 默认按 `mode:'system'` 解析代理，实测并不会真的用上系统代理。
// 代理（Clash 之类）**稳定但慢**，所以把它当默认通道是错的。
//
// 策略：**优先生成直连，只有直连确实不通时才用代理**（用户定稿："能不能不配代理"）。
//   1. AKSO_PROXY / HTTPS_PROXY / HTTP_PROXY 有值 → 强制用该代理（排障用）
//   2. 数据目录 proxy.txt：一行代理串 = 强制代理；存在但为空 = 强制直连（终极兜底开关）
//   3. 否则**自动**：直连探测 GitHub 检查端点，通 → 直连；不通 → 回落到 Windows 系统代理
//      （注册表 Internet Settings；没有系统代理就退回直连并把失败原因记进状态）
//
// ⚠ 必须放行本机回环：壳自己要用 http://127.0.0.1:18765/18766/18767 与 sidecar/CDP 通信，
//   一旦这些请求被塞进代理，整个应用会"网络正常但功能全废"。故 proxyBypassRules 显式含
//   <local> 与 127.0.0.1/localhost。

const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');

const OVERRIDE_FILE = 'proxy.txt'; // 放在壳状态同目录（%APPDATA%\AksoWorkbench）
const BYPASS = '<local>;127.0.0.1;localhost;[::1]';

/** 探测用的端点：更新器"查版本"走的就是这里（Atom feed），通不通直接决定更新能否开始 */
const PROBE_URL = 'https://github.com/jizi-dragon/akso-mogul/releases.atom';

/** 直连探测预算：短于用户对"启动要等多久"的容忍；失败时马上回落代理，不拖启动 */
const PROBE_TIMEOUT_MS = 6000;

/** 规范化用户给的代理串：补 scheme、去尾斜杠。返回 null 表示"不是有效代理"。 */
function normalize(server) {
  const s = String(server || '').trim();
  if (!s) return null;
  if (/^(direct|none|off)$/i.test(s)) return null; // 显式直连
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(s) ? s : `http://${s}`;
  return withScheme.replace(/\/+$/, '');
}

/** 读 Windows 系统代理（注册表）。无代理/读不到都返回 null。 */
function systemProxy() {
  return new Promise((resolve) => {
    const ps =
      "$s=Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings';" +
      "if($s.ProxyEnable -eq 1 -and $s.ProxyServer){$s.ProxyServer}else{''}";
    execFile(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-Command', ps],
      { windowsHide: true, timeout: 4000 },
      (err, stdout) => {
        if (err) return resolve(null);
        resolve(normalize(stdout));
      },
    );
  });
}

function readOverride(dataDir) {
  try {
    const p = path.join(dataDir, OVERRIDE_FILE);
    if (!fs.existsSync(p)) return undefined; // 未配置 → 继续往下找
    const raw = fs.readFileSync(p, 'utf8').split(/\r?\n/)[0];
    return normalize(raw); // 空文件 → null（= 显式直连）
  } catch {
    return undefined;
  }
}

/**
 * 直连探测：用 Chromium 自己的网络栈发一次轻量 GET，判断"现在能不能不走代理"。
 * 带超时（`AbortController`）——直连失败在本机表现为"卡住不返回"，没有超时会挂死启动流程。
 * 探测本身也用来预热 TLS/HTTP2 连接，代价不是纯浪费。
 */
async function probeDirect(net, timeoutMs = PROBE_TIMEOUT_MS) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  const t0 = Date.now();
  try {
    const resp = await net.fetch(PROBE_URL, { signal: ctl.signal });
    return { ok: resp.ok, ms: Date.now() - t0, status: resp.status };
  } catch (e) {
    return { ok: false, ms: Date.now() - t0, error: String((e && e.message) || e).slice(0, 80) };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 解析并应用出网通道。返回 {server, source, probe}（server=null 表示直连）。
 *
 * `net` 用于直连探测（Electron 的 `net` 模块）。不传则跳过探测、按"直连"处理——
 * 这样本模块在纯 node 下也能被单元验证（无 Electron 依赖）。
 * 任何失败都不抛：通道选错最多是更新下不动，不该让应用起不来。
 */
async function apply(session, { dataDir, net, log = () => {} } = {}) {
  let server = null;
  let source = 'direct';
  let probe = null;

  const envProxy = normalize(
    process.env.AKSO_PROXY || process.env.HTTPS_PROXY || process.env.HTTP_PROXY,
  );
  const fileProxy = readOverride(dataDir);

  if (envProxy) {
    server = envProxy;
    source = 'env(强制代理)';
  } else if (fileProxy !== undefined) {
    server = fileProxy;
    source = fileProxy ? 'file(强制代理)' : 'file(强制直连)';
  } else if (!net) {
    source = 'direct(未探测)';
  } else {
    // 自动模式：先试直连（快、且用户偏好），失败才回落系统代理（稳、但慢）
    if (!server) {
      await session.setProxy({ mode: 'direct' });
    }
    probe = await probeDirect(net);
    if (probe.ok) {
      source = `auto(直连可用 ${probe.ms}ms)`;
    } else {
      const sys = await systemProxy();
      if (sys) {
        server = sys;
        source = `auto(直连失败→系统代理 ${sys})`;
      } else {
        source = `auto(直连失败且无系统代理: ${probe.error || probe.status})`;
      }
    }
  }

  try {
    if (server) {
      await session.setProxy({ proxyRules: server, proxyBypassRules: BYPASS });
    } else {
      await session.setProxy({ mode: 'direct' });
    }
    log(`出网通道：${server || '直连'}（来源：${source}）`);
  } catch (e) {
    log(`代理设置失败（忽略，继续直连）：${e && e.message}`);
    server = null;
    source = 'direct(设置失败)';
  }
  return { server, source, probe };
}

module.exports = { apply, normalize, systemProxy, probeDirect, BYPASS, OVERRIDE_FILE, PROBE_URL };
