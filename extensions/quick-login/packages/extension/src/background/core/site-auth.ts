import { LOCAL_KEYS } from '../../shared/constants';
import type { SiteGrant } from '../../shared/types';

const MENU_ID = 'sessionbox-add-site';

function hostPattern(host: string): string {
  return `*://${host}/*`;
}

function siteKey(host: string): string {
  return host.toLowerCase();
}

async function readGrants(): Promise<SiteGrant[]> {
  const stored = await chrome.storage.local.get(LOCAL_KEYS.siteGrants);
  return (stored[LOCAL_KEYS.siteGrants] as SiteGrant[] | undefined) ?? [];
}

async function writeGrants(grants: SiteGrant[]): Promise<void> {
  await chrome.storage.local.set({ [LOCAL_KEYS.siteGrants]: grants });
}

export const siteAuth = {
  async list(): Promise<SiteGrant[]> {
    return readGrants();
  },

  async grant(host: string): Promise<SiteGrant> {
    const normalized = host.toLocaleLowerCase();
    const key = siteKey(normalized);
    const ok = await chrome.permissions.request({ origins: [hostPattern(normalized)] });
    if (!ok) {
      throw new Error(`拒绝授权站点: ${normalized}`);
    }
    const grants = await readGrants();
    if (!grants.some((g) => siteKey(g.host) === key)) {
      grants.push({ host: normalized, grantedAt: Date.now() });
      await writeGrants(grants);
    }
    return { host: normalized, grantedAt: Date.now() };
  },

  async grantCurrentTab(): Promise<string> {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const host = extractHost(tab?.url);
    if (!host) {
      throw new Error('当前标签页不是 http/https 站点');
    }
    await this.grant(host);
    return host;
  },
};

function extractHost(url: string | undefined): string | null {
  if (!url) {
    return null;
  }
  try {
    const u = new URL(url);
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.host : null;
  } catch {
    return null;
  }
}

/* ---------------- scheme（v3.10.9）：站点协议的探测 / hint / 解析 ---------------- */

export type Scheme = 'http' | 'https';

const SCHEME_HINTS_KEY = LOCAL_KEYS.siteSchemes;

/** 站点 scheme hint（添加站点时从用户输入 URL 解析而来；账号创建时优先采用） */
export async function getSchemeHint(host: string): Promise<Scheme | undefined> {
  const stored = await chrome.storage.local.get(SCHEME_HINTS_KEY);
  const map = (stored[SCHEME_HINTS_KEY] as Record<string, Scheme> | undefined) ?? {};
  return map[siteKey(host)];
}

export async function setSchemeHint(host: string, scheme: Scheme): Promise<void> {
  const stored = await chrome.storage.local.get(SCHEME_HINTS_KEY);
  const map = (stored[SCHEME_HINTS_KEY] as Record<string, Scheme> | undefined) ?? {};
  map[siteKey(host)] = scheme;
  await chrome.storage.local.set({ [SCHEME_HINTS_KEY]: map });
}

/** 从用户输入解析 host 与 scheme：支持粘贴完整 URL（http://host[:port]/path）或纯 host */
export function parseSiteInput(raw: string): { host: string; scheme?: Scheme } {
  const trimmed = raw.trim();
  const m = trimmed.match(/^(https?):\/\/([^/]+)(\/.*)?$/i);
  if (m) {
    return { host: m[2].toLowerCase(), scheme: m[1].toLowerCase() as Scheme };
  }
  return { host: trimmed.replace(/\/.*$/, '').toLowerCase() };
}

const PROBE_TIMEOUT_MS = 3000;

async function probeOnce(url: string): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), PROBE_TIMEOUT_MS);
    // HEAD 优先；部分服务器不支持 HEAD 时退 GET（404/405 也算「可达」——网络层通即可）
    const res = await fetch(url, { method: 'HEAD', signal: ctrl.signal, credentials: 'omit' }).catch(() =>
      fetch(url, { method: 'GET', signal: ctrl.signal, credentials: 'omit' }),
    ).finally(() => clearTimeout(timer));
    void res;
    return true;
  } catch {
    return false;
  }
}

/**
 * 探测站点可用协议：https 优先（安全偏好）。
 * 注意：SW fetch 无法忽略证书错误——自签 https 会被误判为不可用而落到 http；
 * 该歧义由「打开失败自学习」兜底纠正（handleOpenError）。
 */
export async function probeScheme(host: string): Promise<Scheme> {
  const hint = await getSchemeHint(host);
  if (hint) {
    return hint;
  }
  if (await probeOnce(`https://${host}/favicon.ico`)) {
    return 'https';
  }
  if (await probeOnce(`http://${host}/favicon.ico`)) {
    return 'http';
  }
  return 'https';
}

export function registerAuthHandlers(): void {
  chrome.contextMenus.create(
    {
      id: MENU_ID,
      title: '将当前站点添加为会话站点',
      contexts: ['page', 'link'],
    },
    () => void chrome.runtime.lastError,
  );

  chrome.contextMenus.onClicked.addListener((info) => {
    if (info.menuItemId === MENU_ID) {
      void siteAuth.grantCurrentTab().catch(() => undefined);
    }
  });

  chrome.commands.onCommand.addListener((command) => {
    if (command === 'add_current_site') {
      void siteAuth.grantCurrentTab().catch(() => undefined);
    }
  });
}