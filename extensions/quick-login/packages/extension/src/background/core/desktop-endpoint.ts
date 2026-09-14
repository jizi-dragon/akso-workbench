/**
 * 桌面端端点发现（ADR-0011 §D-5）。
 *
 * **为什么需要它**：扩展此前把 `http://127.0.0.1:18765` 写死在 sync.ts 里。后果有二：
 *  1. 桌面端换端口（新版本会顺延端口，见 ADR-0011 §D-1）= 扩展静默失联；
 *  2. 18765 上坐着**另一个实例**（旧版本工作台还开着）时，扩展会把凭据
 *     （Fernet 密文 + 原始密钥）发给那一个实例——本机串号，不是网络窃听。
 *
 * **发现链**（按信任度与可用性排序）：
 *  1. 上次发现成功的值（缓存，立即可用）；
 *  2. 壳的控制服务 `GET /whoami`——控制服务是**壳自己的**，所以它的回答天然比
 *     "18765 上是谁"可信；按窗口从 18767 起试，带 `app` 校验；
 *  3. 固定 `18765` 兜底——保证**旧桌面端**（没有 /whoami 的版本）仍然可用。
 *
 * **权限**：manifest 已是 `host_permissions: ["<all_urls>"]`，探测不需要新增权限。
 * 若将来收窄成白名单，必须先把控制服务端口段一并申明，否则本模块会静默失效。
 *
 * 失败语义沿用既有约定：**桌面不可达就静默跳过**（本地数据保持可用，离线回退），
 * 因此本模块的函数都不抛错，只返回"没找到"。
 */

const DEFAULT_SERVER_PORT = 18765;
/** 控制服务的基准端口与探测窗口（与 desktop/ports.js 的 BASE.control / MAX_DRIFT 对应） */
const CONTROL_BASE_PORT = 18767;
const CONTROL_PROBE_WINDOW = 4;
/** 单次探测超时：本机回环，超过这个数说明没在听 */
const PROBE_TIMEOUT_MS = 700;
/** 发现结果缓存时长：避免每 2s 轮询都去探一遍控制服务 */
const CACHE_TTL_MS = 60_000;

const CACHE_KEY = 'akso:desktopEndpoint';

interface Endpoint {
  base: string;
  port: number;
  at: number;
}

let memo: Endpoint | null = null;
let discovering: Promise<Endpoint> | null = null;

function fallback(): Endpoint {
  return { base: `http://127.0.0.1:${DEFAULT_SERVER_PORT}`, port: DEFAULT_SERVER_PORT, at: Date.now() };
}

/** 带超时的 GET —— fetch 在本机回环上遇到"没人听"通常很快失败，但半开的端口会挂很久 */
async function fetchJson(url: string, timeoutMs = PROBE_TIMEOUT_MS): Promise<any | null> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { cache: 'no-store', signal: ctl.signal });
    if (!resp.ok) return null;
    return await resp.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function readCache(): Promise<Endpoint | null> {
  try {
    const raw = (await chrome.storage.local.get(CACHE_KEY))[CACHE_KEY] as Endpoint | undefined;
    if (!raw || typeof raw.base !== 'string' || typeof raw.port !== 'number') return null;
    return raw;
  } catch {
    return null;
  }
}

async function writeCache(ep: Endpoint): Promise<void> {
  try {
    await chrome.storage.local.set({ [CACHE_KEY]: ep });
  } catch {
    // 缓存失败不影响本次使用
  }
}

/** 问壳的控制服务要服务端端口 */
async function askShell(): Promise<Endpoint | null> {
  for (let i = 0; i < CONTROL_PROBE_WINDOW; i += 1) {
    const controlPort = CONTROL_BASE_PORT + i;
    // 串行探测：按端口顺序取第一个应答的壳
    const data = await fetchJson(`http://127.0.0.1:${controlPort}/whoami`);
    if (!data || data.app !== 'AksoWorkbench') continue;
    const port = Number(data.serverPort);
    if (!Number.isInteger(port) || port <= 0 || port > 65535) continue;
    return { base: `http://127.0.0.1:${port}`, port, at: Date.now() };
  }
  return null;
}

/**
 * 当前桌面端 base URL（形如 `http://127.0.0.1:18765`，**不带尾斜杠**）。
 *
 * 永不抛错：最差情况返回固定 18765（与改造前行为一致）。调用方只管拼 path。
 */
export async function desktopBase(): Promise<string> {
  if (memo && Date.now() - memo.at < CACHE_TTL_MS) return memo.base;

  if (!discovering) {
    discovering = (async () => {
      const cached = await readCache();
      if (cached && Date.now() - cached.at < CACHE_TTL_MS) return cached;
      const found = (await askShell()) ?? fallback();
      await writeCache(found);
      return found;
    })().finally(() => { discovering = null; });
  }
  const ep = await discovering;
  memo = ep;
  return ep.base;
}

/**
 * 让缓存立即失效——用于"请求失败"路径：桌面端可能刚重启并换了端口，
 * 下次调用会重新问壳（最长一次探测窗口的延迟，见 ADR-0011 §6.1 已知边界）。
 */
export function invalidateDesktopEndpoint(): void {
  memo = null;
}

/** 当前已知的服务端口（未发现时 = 固定基准）。供 parallel-session 的同步宿主白名单使用。 */
export async function desktopServerPort(): Promise<number> {
  await desktopBase();
  return memo ? memo.port : DEFAULT_SERVER_PORT;
}

export const DESKTOP_DEFAULT_PORT = DEFAULT_SERVER_PORT;
