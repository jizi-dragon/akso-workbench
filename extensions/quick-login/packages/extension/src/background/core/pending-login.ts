import { SESSION_KEYS } from '../../shared/constants';

/**
 * 待自动登录凭证的交接通道：绑定页签打开后，内容脚本按 tabId 主动索取。
 *
 * 为什么落在 storage.session：MV3 service worker 会被回收，内存缓存不可靠；
 * storage.session 随浏览器会话存活且不落盘。
 *
 * 与账号模型解耦：并行账号（par.*）与旧会话模型（session.*）共用同一约定，
 * 故本模块不依赖任何一方——此前这段键格式与读写逻辑在 navigation.ts 与
 * parallel-session.ts 各写一份（改动一处即静默失配），现收敛为单一真源。
 */
const AUTO_LOGIN_TTL = 60_000;

function pendingKey(tabId: number): string {
  return `${SESSION_KEYS.pendingAutoLogins}:${tabId}`;
}

/** 缓存待自动登录凭证（service worker 回收后不丢失） */
export async function setPendingAutoLogin(
  tabId: number,
  username: string,
  password: string,
): Promise<void> {
  await chrome.storage.session.set({
    [pendingKey(tabId)]: { username, password, at: Date.now() },
  });
}

/** 内容脚本（含各 iframe frame）就绪后主动索取凭证；超时视为失效并清理 */
export async function getPendingAutoLogin(
  tabId: number,
): Promise<{ username: string; password: string } | null> {
  const key = pendingKey(tabId);
  const stored = await chrome.storage.session.get(key);
  const entry = stored[key] as { username: string; password: string; at: number } | undefined;
  if (!entry) {
    return null;
  }
  if (Date.now() - entry.at > AUTO_LOGIN_TTL) {
    await chrome.storage.session.remove(key);
    return null;
  }
  return { username: entry.username, password: entry.password };
}

/** 页签关闭时清理凭证（navigation 的 tabs.onRemoved 调用） */
export async function clearPendingAutoLogin(tabId: number): Promise<void> {
  await chrome.storage.session.remove(pendingKey(tabId));
}

/**
 * 装载页签生命周期清理，由 service-worker 调用一次。
 *
 * 原属 navigation.ts（旧会话模型的装载函数）：随会话模型退役，只剩「页签关闭清凭证」
 * 这一条与账号模型无关的职责，故收归本模块（凭证有 60s TTL 兜底，此处是即时回收）。
 */
export function registerPendingLoginHandlers(): void {
  chrome.tabs.onRemoved.addListener((tabId) => {
    void clearPendingAutoLogin(tabId);
  });
}
