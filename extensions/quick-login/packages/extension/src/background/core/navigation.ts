import { SESSION_KEYS, CONTENT_MESSAGE } from '../../shared/constants';
import { accountRegistry } from './account-registry';
import { sessionManager } from './session-manager';
import { setTabTitle } from '../tabs/tab-title';
import type { Session } from '../../shared/types';

export interface TabBinding {
  sessionId: string;
  host: string;
}

/** 内存 + session 持久化的 tabId ↔ sessionId 绑定表（仅用于复用标签页） */
const bindings = new Map<number, TabBinding>();

async function readState(): Promise<void> {
  const stored = await chrome.storage.session.get([SESSION_KEYS.sessionTabBindings]);
  const map = stored[SESSION_KEYS.sessionTabBindings] as Record<string, TabBinding> | undefined;
  bindings.clear();
  if (map) {
    for (const [tabId, binding] of Object.entries(map)) {
      bindings.set(Number(tabId), binding);
    }
  }
}

async function persistBindings(): Promise<void> {
  const map: Record<string, TabBinding> = {};
  for (const [tabId, binding] of bindings) {
    map[String(tabId)] = binding;
  }
  await chrome.storage.session.set({ [SESSION_KEYS.sessionTabBindings]: map });
}

/** 根据 Cookie 记录构造可用于 chrome.cookies.remove 的 URL */
function cookieUrl(c: chrome.cookies.Cookie): string {
  const domain = c.domain.startsWith('.') ? c.domain.slice(1) : c.domain;
  const protocol = c.secure ? 'https' : 'http';
  const path = c.path?.startsWith('/') ? c.path : '/';
  return `${protocol}://${domain}${path}`;
}

/**
 * 登出：清空该 host 的登录态。
 * 实测（Akso eGMP）：前端凭 Cookie 中的 token 鉴权，删 Cookie 即失去登录态；
 * localStorage 中的用户信息会在下次登录时被覆盖，这里一并清空以干净登出。
 */
async function clearLoginState(tabId: number, host: string): Promise<void> {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      func: () => window.localStorage.clear(),
      world: 'MAIN',
    });
  } catch {
    // 页面尚未加载（新建标签）或不可注入，忽略；登录时会覆盖 localStorage
  }
  const cookies = await chrome.cookies.getAll({ domain: host });
  await Promise.all(
    cookies.map((c) => chrome.cookies.remove({ url: cookieUrl(c), name: c.name }).catch(() => null)),
  );
}

const AUTO_LOGIN_TTL = 60_000;

function pendingKey(tabId: number): string {
  return `${SESSION_KEYS.pendingAutoLogins}:${tabId}`;
}

/** 缓存待自动登录凭证到 chrome.storage.session（service worker 回收后不丢失） */
async function setPendingAutoLogin(tabId: number, username: string, password: string): Promise<void> {
  await chrome.storage.session.set({
    [pendingKey(tabId)]: { username, password, at: Date.now() },
  });
}

/** content 脚本（含各 iframe frame）就绪后主动索取凭证；超时视为失效 */
async function getPendingAutoLogin(tabId: number): Promise<{ username: string; password: string } | null> {
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

/** 向已绑定标签下发自动登录：先缓存凭证（供内容脚本主动拉取），再尝试即时下发 */
async function triggerAutoLogin(tabId: number, username: string, password: string): Promise<void> {
  await setPendingAutoLogin(tabId, username, password);
  try {
    await chrome.tabs.sendMessage(tabId, {
      type: CONTENT_MESSAGE.autoLogin,
      username,
      password,
    });
  } catch {
    // 内容脚本未就绪，忽略；其就绪后会经 sb:autoLoginRequest 主动拉取凭证
  }
}

/** 标题改写：权威写入 + 通知内容脚本持续维持（SPA 内导航会重置标题） */
async function applyTitle(tabId: number, alias: string): Promise<void> {
  await setTabTitle(tabId, alias);
  try {
    await chrome.tabs.sendMessage(tabId, { type: CONTENT_MESSAGE.setTitle, alias });
  } catch {
    // 内容脚本未就绪，忽略；setTabTitle 已权威写入
  }
}

/** 查找该会话已打开的标签页（用于复用而非盲目新建） */
function findOpenTab(sessionId: string): number | null {
  for (const [tabId, binding] of bindings) {
    if (binding.sessionId === sessionId) {
      return tabId;
    }
  }
  return null;
}

export const navigation = {
  async bind(tabId: number, sessionId: string, host: string): Promise<void> {
    bindings.set(tabId, { sessionId, host });
    await persistBindings();
  },

  /**
   * 切换账号：登出当前登录态 → 导航登录页 → 用保存凭证自动登录 → 改标题。
   * 这是「免密快速切换」的核心，替代原「Cookie 罐焦点仲裁」。
   */
  async switchAccount(
    session: Session,
    credentials?: { username: string; password: string },
  ): Promise<{ tabId: number; reused: boolean }> {
    const scheme = session.scheme ?? 'https';
    const loginUrl = `${scheme}://${session.siteHost}/login`;
    const existingTabId = findOpenTab(session.id);

    let tabId: number;
    let reused: boolean;
    if (existingTabId !== null) {
      tabId = existingTabId;
      reused = true;
    } else {
      const tab = await chrome.tabs.create({ url: loginUrl });
      tabId = tab.id!;
      await this.bind(tabId, session.id, session.siteHost);
      reused = false;
    }

    await clearLoginState(tabId, session.siteHost);
    await chrome.tabs.update(tabId, { url: loginUrl, active: true });

    const alias = (await accountRegistry.getAlias(session.id)) ?? '';
    await applyTitle(tabId, alias);

    if (credentials) {
      await triggerAutoLogin(tabId, credentials.username, credentials.password);
    }
    return { tabId, reused };
  },

  /** 内容脚本（含 iframe frame）就绪后主动索取自动登录凭证（只读共享，超时失效） */
  getPendingAutoLogin,

  /** 旧会话模型的打开失败自学习（v3.10.9）：session.scheme 翻转写回并原页签重开 */
  async handleSessionOpenError(tabId: number): Promise<boolean> {
    const binding = bindings.get(tabId);
    if (!binding) {
      return false;
    }
    const session = await sessionManager.get(binding.sessionId);
    if (!session) {
      return false;
    }
    const current = session.scheme ?? 'https';
    const flipped = current === 'https' ? 'http' : 'https';
    await sessionManager.updateScheme(binding.sessionId, flipped);
    const url = `${flipped}://${session.siteHost}/login`;
    await chrome.tabs.update(tabId, { url });
    return true;
  },
};

/** 装载事件监听，由 service-worker 调用一次 */
export function registerNavigationHandlers(): void {
  void readState();
  // service-worker 每次唤醒都重新装载绑定
  chrome.runtime.onStartup.addListener(() => {
    void readState();
  });

  // 标签关闭：清除绑定与待登录凭证
  chrome.tabs.onRemoved.addListener((tabId) => {
    void chrome.storage.session.remove(pendingKey(tabId));
    if (bindings.delete(tabId)) {
      void persistBindings();
    }
  });
}
