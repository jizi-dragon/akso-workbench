/**
 * 访问令牌客户端（P0-1）：`/extension/*` 现在需要令牌，本模块负责**领取 + 携带 + 失效重取**。
 *
 * ## 为什么需要它
 *
 * `GET /extension/snapshot` 会下发 Fernet 密钥 + 全部密文凭据。此前整条 `/extension/*`
 * 无鉴权 → 本机任意进程一次 GET 即等于拿到全部账号明文（CODEBASE_OVERVIEW R1）。
 * 服务端现在按**路径前缀**统一要求 `X-Workbench-Token`（`workbench/api/extension_auth.py`）。
 *
 * ## 三件事，缺一不可
 *
 * 1. **领取**：`POST /extension/hello` → `{token}`（该端点是**唯一**免令牌的实质端点，
 *    只回令牌与版本号，不含账号数据）。令牌落 `chrome.storage.local`，跨 SW 回收存活。
 * 2. **携带**：每个 `/extension/*` 请求带上令牌头。
 * 3. **失效重取**：服务端换令牌/换机器/手工清库后，旧令牌会 401。
 *    `extension_auth_required` 这个**机器可读 code** 就是给这一步用的——
 *    收到它 → 丢掉缓存令牌 → 重新握手 → **重试一次**。
 *    没有这一步，表现会是"快捷登录莫名失灵"（401 被当普通网络错误吞进 catch）。
 *
 * ## 向后兼容（ADR-0013 的"协议类标识"纪律）
 *
 * - **旧桌面端**（不认识 `/extension/hello`，返回 404/405）：视为"不需要令牌"，
 *   照常发不带令牌的请求。新扩展 + 旧服务端必须能用。
 * - **旧扩展 + 新服务端**：服务端有 grace 宽限期（默认 24h）照常放行，
 *   过后严格拒绝并提示用户重新加载扩展。
 *
 * ## 为什么不做"每次都握手"
 *
 * `tick()` 每 2s 跑一次、长轮询常驻。每次握手 = 多一个 HTTP 往返。
 * 令牌跨重启稳定（服务端落 settings 表），所以缓存起来是对的；只有 401 才重取。
 */

import { LOCAL_KEYS } from '../../shared/constants';
import { desktopBase } from './desktop-endpoint';

/** 令牌请求头。必须与服务端 `extension_auth.TOKEN_HEADER` **逐字一致**（小写）。 */
export const TOKEN_HEADER = 'X-Workbench-Token';

/** 服务端 401 响应体里的机器可读 code（见 `extension_auth.AUTH_ERROR_CODE`）。 */
export const AUTH_ERROR_CODE = 'extension_auth_required';

const TOKEN_KEY = LOCAL_KEYS.desktopToken;
const HELLO_PATH = '/extension/hello';
/** 握手超时：本机回环，超过这个数说明桌面没在听 */
const HELLO_TIMEOUT_MS = 3000;

let memo: string | null = null;
/** 并发去重：tick 与长轮询流可能同时撞到 401，不该并发发两次握手 */
let handshaking: Promise<string | null> | null = null;

async function readStored(): Promise<string | null> {
  try {
    const raw = (await chrome.storage.local.get(TOKEN_KEY))[TOKEN_KEY];
    return typeof raw === 'string' && raw ? raw : null;
  } catch {
    return null;
  }
}

async function writeStored(token: string | null): Promise<void> {
  try {
    if (token) await chrome.storage.local.set({ [TOKEN_KEY]: token });
    else await chrome.storage.local.remove(TOKEN_KEY);
  } catch {
    // 落盘失败不影响本次使用（下次重新握手）
  }
}

/**
 * 令牌缓存作废。**只在 401 路径调用**——正常路径不要动它，
 * 否则每个请求都退化成一次额外握手。
 */
export async function invalidateDesktopToken(): Promise<void> {
  memo = null;
  await writeStored(null);
}

/**
 * 领取（或返回缓存的）令牌。
 *
 * 返回 `null` 有两种含义，调用方**不必区分**（都表现为"不带令牌发请求"）：
 * - 旧桌面端：`/extension/hello` 不存在（404/405）→ 该服务端不要求令牌；
 * - 桌面不可达：离线回退，本地数据照常可用（与 `desktopBase()` 同一条纪律）。
 */
export async function desktopToken(): Promise<string | null> {
  if (memo) return memo;
  const cached = await readStored();
  if (cached) {
    memo = cached;
    return memo;
  }
  if (!handshaking) {
    handshaking = (async () => {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), HELLO_TIMEOUT_MS);
      try {
        const base = await desktopBase();
        const resp = await fetch(`${base}${HELLO_PATH}`, {
          method: 'POST',
          cache: 'no-store',
          signal: ctl.signal,
        });
        // 404/405 = 旧桌面端不认识握手端点 → 不要求令牌，正常发请求即可
        if (!resp.ok) return null;
        const data = await resp.json();
        const token = typeof data?.token === 'string' && data.token ? data.token : null;
        if (token) {
          memo = token;
          await writeStored(token);
        }
        return token;
      } catch {
        return null; // 桌面不可达：离线回退
      } finally {
        clearTimeout(timer);
        handshaking = null;
      }
    })();
  }
  return handshaking;
}

/** 令牌请求头（拿不到令牌时为空对象——不写 `undefined`，免得 fetch 把它序列化成字符串）。 */
export async function authHeaders(): Promise<Record<string, string>> {
  const token = await desktopToken();
  return token ? { [TOKEN_HEADER]: token } : {};
}

/** 走一遍"可能是 401"的处理：作废令牌 → 重新握手 → 用新令牌重试**一次**。 */
async function retryAfterAuthFailure(
  url: string,
  init: RequestInit,
  response: Response
): Promise<Response | null> {
  let body: any = null;
  try {
    body = await response.clone().json();
  } catch {
    return null; // 不是我们的 401（无 JSON 体）→ 交给调用方当普通错误
  }
  if (body?.code !== AUTH_ERROR_CODE) return null;

  await invalidateDesktopToken();
  const token = await desktopToken();
  if (!token) return null; // 握手也拿不到（旧桌面端/离线）→ 不重试
  const headers = { ...(init.headers as Record<string, string> | undefined), [TOKEN_HEADER]: token };
  try {
    return await fetch(url, { ...init, headers });
  } catch {
    return null;
  }
}

/**
 * 带令牌的 fetch：**所有** `/extension/*` 请求都应经它发出。
 *
 * `path` 以 `/` 开头（与既有 `getJson` 的调用约定一致），内部拼 `desktopBase()`。
 * 返回 `null` 表示"请求没成功"（离线 / 桌面端拒绝 / 旧版无此端点）——与既有
 * `getJson` 的语义一致：**调用方一律按"离线回退"处理，不抛错**。
 */
export async function authFetch(path: string, init: RequestInit = {}): Promise<Response | null> {
  const base = await desktopBase();
  const url = `${base}${path}`;
  const headers = { ...(init.headers as Record<string, string> | undefined), ...(await authHeaders()) };
  try {
    const resp = await fetch(url, { ...init, headers });
    if (resp.status !== 401) return resp;
    return (await retryAfterAuthFailure(url, { ...init, headers }, resp)) ?? resp;
  } catch {
    return null;
  }
}
