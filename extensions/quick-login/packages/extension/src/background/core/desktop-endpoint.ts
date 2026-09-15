/**
 * 桌面端端点（ADR-0012：端口**固定**，故本模块只做"确认身份 + 回退"）。
 *
 * **演进**：这里曾实现过完整的"按窗口探测"（ADR-0011 §D-5）——因为当时桌面端会
 * 在端口被占时**顺延**（18767、18768…），扩展只能挨个试。**2026-09-14 撤回顺延**
 * （用户定稿：就固定端口号），于是探测窗口随之取消，只剩一次确认请求。
 *
 * **现在做两件事**：
 *  1. 打一次 `GET http://127.0.0.1:18767/whoami`，核对 `app === 'Workbench'`；
 *  2. 拿到答案就用；拿不到就回退固定 `18765`（保证旧桌面端仍可用、离线也不炸）。
 *
 * **为什么还留这一次请求**（而不是直接把 18765 写死）：它回答的是**身份**——
 * "18765 上坐着的是不是我们的服务端"。凭据（Fernet 密文 + 原始密钥）会经过这条通道，
 * 而密钥就在本机数据库里，所以"发给了另一个实例"不是网络窃听问题（PITFALLS #23）。
 * 固定端口消除了"顺延导致的失联"，但没有消除"端口上是谁"，故这一步保留。
 *
 * **权限**：manifest 已是 `host_permissions: ["<all_urls>"]`，探测不需要新增权限。
 *
 * 失败语义沿用既有约定：**桌面不可达就静默跳过**（本地数据保持可用），函数不抛错。
 */

const DEFAULT_SERVER_PORT = 18765;
/** 壳控制服务的固定端口（与 desktop/ports.js 的 PORTS.control 一致） */
const CONTROL_PORT = 18767;
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

/** 问壳的控制服务：要的不是"端口是多少"，而是"18765 上是不是我们的服务端" */
async function askShell(): Promise<Endpoint | null> {
  const data = await fetchJson(`http://127.0.0.1:${CONTROL_PORT}/whoami`);
  if (!data || data.app !== 'Workbench') return null;
  const port = Number(data.serverPort);
  if (!Number.isInteger(port) || port <= 0 || port > 65535) return null;
  return { base: `http://127.0.0.1:${port}`, port, at: Date.now() };
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
 * 让缓存立即失效——用于"请求失败"路径：下次调用会重新问壳确认身份。
 *
 * （端口已固定，所以这里不再是"桌面端换了端口"的补救；它现在只用于
 * "上次答案是错的/过期了"这种情形，最长一次请求的延迟。）
 */
export function invalidateDesktopEndpoint(): void {
  memo = null;
}

/** 当前已知的服务端口（未发现时 = 固定端口）。供 parallel-session 的同步宿主白名单使用。 */
export async function desktopServerPort(): Promise<number> {
  await desktopBase();
  return memo ? memo.port : DEFAULT_SERVER_PORT;
}

export const DESKTOP_DEFAULT_PORT = DEFAULT_SERVER_PORT;
