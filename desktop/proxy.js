// 网络代理自动接管（桌面壳）
//
// 为什么需要这个文件：electron-updater 的下载链是
//   api.github.com（查版本）→ github.com/…/releases/download/…（302 跳转）→ CDN
// 实测（本机、直连）第一跳通、CDN 通，唯独中间的 `github.com` 超时 —— 于是"能发现新版、
// 却永远下载不下来"。而 Chromium 默认按 `mode:'system'` 解析代理，实测在这台机器上并没有
// 走系统代理（IE 设置里写着 127.0.0.1:7890，请求仍然直连超时），所以必须显式设置。
//
// 策略（按优先级）：
//   1. 环境变量 AKSO_PROXY / HTTPS_PROXY / HTTP_PROXY（显式覆盖，便于排障）
//   2. 数据目录下的 proxy.txt（一行，形如 http://127.0.0.1:7890；空文件=强制直连）
//   3. Windows 系统代理（注册表 Internet Settings：ProxyEnable + ProxyServer）
//   4. 都没有 → 明确直连（ProxyHandler({}) 语义的 mode:'direct'）
//
// ⚠ 必须放行本机回环：壳自己要用 http://127.0.0.1:18765/18766/18767 与 sidecar/CDP 通信，
//   一旦这些请求被塞进代理，整个应用会"网络正常但功能全废"。故 proxyBypassRules 显式含
//   <local> 与 127.0.0.1/localhost，并且在设置后主动探测一次本地服务是否仍可达。

const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');

const OVERRIDE_FILE = 'proxy.txt'; // 放在壳状态同目录（%APPDATA%\AksoWorkbench）
const BYPASS = '<local>;127.0.0.1;localhost;[::1]';

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
 * 解析并应用代理。返回 {server, source}（server=null 表示直连）。
 * 任何失败都不抛：代理配错不该让应用起不来。
 */
async function apply(session, { dataDir, log = () => {} } = {}) {
  let server = null;
  let source = 'direct';

  const envProxy = normalize(
    process.env.AKSO_PROXY || process.env.HTTPS_PROXY || process.env.HTTP_PROXY,
  );
  const fileProxy = readOverride(dataDir);

  if (envProxy) {
    server = envProxy;
    source = 'env';
  } else if (fileProxy !== undefined) {
    server = fileProxy;
    source = fileProxy ? 'file' : 'file(直连)';
  } else {
    const sys = await systemProxy();
    if (sys) {
      server = sys;
      source = 'system';
    }
  }

  try {
    if (server) {
      await session.setProxy({ proxyRules: server, proxyBypassRules: BYPASS });
    } else {
      await session.setProxy({ mode: 'direct' });
    }
    log(`代理：${server || '直连'}（来源：${source}）`);
  } catch (e) {
    log(`代理设置失败（忽略，继续直连）：${e && e.message}`);
    server = null;
    source = 'direct(设置失败)';
  }
  return { server, source };
}

module.exports = { apply, normalize, systemProxy, BYPASS, OVERRIDE_FILE };
