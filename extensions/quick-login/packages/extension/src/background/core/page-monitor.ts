/**
 * Page Monitor —— 配置页监听 / 主体名解析 / 最近 5 个记录（v3.11）。
 *
 * 数据流（对应 docs/FEASIBILITY-RECENT-PAGES.md 实测映射表）：
 *   MAIN 壳嗅探名称型 API（BasicObjectDetail / GetWorkflowBasic / 菜单树等）
 *   → 桥上行 `pageNames` → 本模块合并进 storage.session 的 `ql:pageNames`（host → guid→name 扁平表；
 *     菜单项/菜单组/对象/工作流的 id 都是全局唯一 guid，一张表通吃）
 *   → `tabs.onUpdated` 的 URL 变化经 L1 路由分类器解析出 {页面类型, 后缀, guid}
 *   → guid 精确命中名称表 → 主体名；生命周期类页面（guid 与名称 guid 不同族）走
 *     「加载窗口候选」：该页加载期间最新一条详情类名称（BasicObjectDetail 等）
 *   → 复合页签标题 `账号别名 · 主体名·类型`（经既有 title 管线下发）
 *   → 同时写入最近配置页 MRU（storage.local `ql:recentPages`，按 host 分组，容量 5）。
 *
 * 隔离纪律：跳转/标题发生在当前标签页（tabId 不变），账号绑定与六平面规则无缝延续；
 * MRU 条目带来源账号别名仅供展示，不参与任何账号切换决策。
 */
import { LOCAL_KEYS, RECENT_PAGES_MAX, SESSION_KEYS } from '../../shared/constants';
import type { RecentPageEntry } from '../../shared/messages';
import { CONTENT_MESSAGE } from '../../shared/constants';
import { parallelStore } from './parallel-store';
import { setTabTitle } from '../tabs/tab-title';

/** 名称表在 storage.session 中的键：{ [host]: Record<guid, name> } */
const PAGE_NAMES_KEY = 'ql:pageNames';
/** 加载窗口候选的有效期（超过视为陈旧，不用作生命周期主体名） */
const WINDOW_CANDIDATE_TTL = 60_000;

interface PageClass {
  pageType: string;
  suffix: string;
  /** 主体 guid（menu/guid 类）；window 类为路径 guid（不用于查表） */
  guid: string | null;
  /** guid=直接查表；menu=菜单树查表；window=加载窗口候选 */
  kind: 'guid' | 'menu' | 'window';
}

/** L1 路由分类器（映射表见 docs/FEASIBILITY-RECENT-PAGES.md §〇，全部为实测样本） */
export function classifyConfigPage(rawUrl: string): PageClass | null {
  let u: URL;
  try {
    u = new URL(rawUrl);
  } catch {
    return null;
  }
  const path = u.pathname;
  if (path === '/web' || path === '/web/') {
    const d = u.searchParams.get('display');
    return d ? { pageType: '对象工作区', suffix: '对象', guid: d, kind: 'guid' } : null;
  }
  if (path === '/web/view') {
    const mid = u.searchParams.get('mid');
    return mid ? { pageType: '业务菜单', suffix: '菜单', guid: mid, kind: 'menu' } : null;
  }
  if (path.startsWith('/admin/config/basic-objects/edit/')) {
    const id = u.searchParams.get('id');
    return id ? { pageType: '对象配置', suffix: '对象', guid: id, kind: 'guid' } : null;
  }
  const lifecycle = /^\/admin\/config\/lifecycle\/([0-9a-f-]{16,})/i.exec(path);
  if (lifecycle) {
    return { pageType: '生命周期配置', suffix: '生命周期', guid: lifecycle[1], kind: 'window' };
  }
  if (path.startsWith('/admin/config/workflow/edit')) {
    const id = u.searchParams.get('id');
    return id ? { pageType: '工作流配置', suffix: '工作流', guid: id, kind: 'guid' } : null;
  }
  return null;
}

interface TabState {
  host: string;
  url: string;
  cls: PageClass;
  alias: string;
  /** 已解析主体名（未解析时 undefined，等名称表/窗口候选到达后原地升级） */
  subject?: string;
}

const tabs = new Map<number, TabState>();
/** 每页签「加载窗口候选」：详情类名称 API 的最新结果（生命周期页主体名来源） */
const windowCandidate = new Map<number, { name: string; ts: number }>();

/* ---------------- 名称表（storage.session） ---------------- */

async function loadNames(host: string): Promise<Record<string, string>> {
  const o = await chrome.storage.session.get(PAGE_NAMES_KEY);
  const all = (o[PAGE_NAMES_KEY] as Record<string, Record<string, string>> | undefined) ?? {};
  return all[host] ?? {};
}

async function mergeNames(host: string, incoming: { name: string; id: string }[]): Promise<boolean> {
  if (!incoming.length) {
    return false;
  }
  const o = await chrome.storage.session.get(PAGE_NAMES_KEY);
  const all = (o[PAGE_NAMES_KEY] as Record<string, Record<string, string>> | undefined) ?? {};
  const cur = all[host] ?? {};
  let changed = false;
  for (const { name, id } of incoming) {
    const gid = String(id ?? '').trim();
    const gname = String(name ?? '').trim();
    if (gid && gname && cur[gid] !== gname) {
      cur[gid] = gname;
      changed = true;
    }
  }
  if (changed) {
    all[host] = cur;
    await chrome.storage.session.set({ [PAGE_NAMES_KEY]: all });
  }
  return changed;
}

/* ---------------- 标题与最近记录 ---------------- */

/** 页签当前账号别名（并行绑定表 → 账号 tabName/username；未绑定返回空串） */
async function aliasOfTab(tabId: number): Promise<string> {
  try {
    const o = await chrome.storage.session.get(SESSION_KEYS.parTabBindings);
    const map = (o[SESSION_KEYS.parTabBindings] as Record<string, { accountId: string }> | undefined) ?? {};
    const b = map[String(tabId)];
    if (!b?.accountId) {
      return '';
    }
    const account = await parallelStore.get(b.accountId);
    return account.tabName || account.username || '';
  } catch {
    return '';
  }
}

async function pushTitle(tabId: number, fullTitle: string): Promise<void> {
  try {
    await setTabTitle(tabId, fullTitle);
  } catch {
    // 页面不可注入忽略
  }
  try {
    await chrome.tabs.sendMessage(tabId, { type: CONTENT_MESSAGE.setTitle, alias: fullTitle });
  } catch {
    // 内容脚本未就绪忽略（下次 onUpdated 重申）
  }
}

async function recordRecent(host: string, entry: RecentPageEntry): Promise<void> {
  const o = await chrome.storage.local.get(LOCAL_KEYS.recentPages);
  const all = (o[LOCAL_KEYS.recentPages] as Record<string, RecentPageEntry[]> | undefined) ?? {};
  const list = all[host] ?? [];
  const dedupKey = `${entry.pageType}|${entry.subject}`;
  const next = [entry, ...list.filter((x) => `${x.pageType}|${x.subject}` !== dedupKey)].slice(0, RECENT_PAGES_MAX);
  all[host] = next;
  await chrome.storage.local.set({ [LOCAL_KEYS.recentPages]: all });
}

/** 解析主体名并应用标题 + MRU；返回是否解析成功 */
async function applySubject(tabId: number, st: TabState): Promise<boolean> {
  const names = await loadNames(st.host);
  let subject: string | undefined;
  if (st.cls.kind === 'window') {
    const cand = windowCandidate.get(tabId);
    if (cand && Date.now() - cand.ts <= WINDOW_CANDIDATE_TTL) {
      subject = cand.name;
    }
  } else if (st.cls.guid) {
    subject = names[st.cls.guid];
  }
  if (!subject) {
    // 未命中：先显示类型 + guid 前缀占位，名称到达后原地升级
    const placeholder = st.cls.guid ? `${st.cls.pageType} · ${st.cls.guid.slice(0, 8)}` : st.cls.pageType;
    await pushTitle(tabId, st.alias ? `${st.alias} · ${placeholder}` : placeholder);
    return false;
  }
  const changed = st.subject !== subject;
  st.subject = subject;
  const label = `${subject}·${st.cls.suffix}`;
  await pushTitle(tabId, st.alias ? `${st.alias} · ${label}` : label);
  if (changed) {
    await recordRecent(st.host, {
      url: st.url,
      pageType: st.cls.pageType,
      suffix: st.cls.suffix,
      subject,
      accountAlias: st.alias,
      ts: Date.now(),
    });
  }
  return true;
}

/* ---------------- 上行入口：名称嗅探 ---------------- */

/** 详情类 API 路径（窗口候选只认这些；列表/树类进名称表但不作候选） */
const DETAIL_API = /BasicObjectDetail|GetWorkflowBasic\?|UserView\/GetView\?/i;

async function ingestNames(tabId: number | undefined, names: { name: string; id: string }[], src: string): Promise<void> {
  if (!tabId) {
    return;
  }
  let host = tabs.get(tabId)?.host;
  if (!host) {
    try {
      const tab = await chrome.tabs.get(tabId);
      host = tab.url ? new URL(tab.url).host : undefined;
    } catch {
      return;
    }
  }
  if (!host) {
    return;
  }
  const changed = await mergeNames(host, names);
  if (DETAIL_API.test(src)) {
    const first = names.find((n) => n.name && n.id);
    if (first) {
      windowCandidate.set(tabId, { name: first.name, ts: Date.now() });
    }
  }
  // 同 host 全部未解析页签重新解析（预缓存的列表名称也能命中）
  const st = tabs.get(tabId);
  if (st) {
    await applySubject(tabId, st);
  }
  if (changed) {
    for (const [tid, other] of tabs) {
      if (tid !== tabId && other.host === host && !other.subject) {
        await applySubject(tid, other);
      }
    }
  }
}

/* ---------------- tabs.onUpdated：导航监听 ---------------- */

async function onTabUpdated(tabId: number, tab: chrome.tabs.Tab): Promise<void> {
  const url = tab.url;
  if (!url || !/^https?:/i.test(url)) {
    return;
  }
  const cls = classifyConfigPage(url);
  if (!cls) {
    if (tabs.delete(tabId)) {
      windowCandidate.delete(tabId);
    }
    return;
  }
  let host = '';
  try {
    host = new URL(url).host;
  } catch {
    return;
  }
  const alias = await aliasOfTab(tabId);
  const st: TabState = { host, url, cls, alias };
  tabs.set(tabId, st);
  await applySubject(tabId, st);
}

/** 由 service-worker 装载一次 */
export function registerPageMonitorListeners(): void {
  chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (!changeInfo.url && !changeInfo.status && !changeInfo.title) {
      return;
    }
    if (!tab.url) {
      return;
    }
    void onTabUpdated(tabId, tab).catch(() => undefined);
  });
  chrome.tabs.onRemoved.addListener((tabId) => {
    tabs.delete(tabId);
    windowCandidate.delete(tabId);
  });
}

/* ---------------- 轮盘数据面 ---------------- */

/** 当前页签所属 host 的最近配置页（轮盘展示用） */
export async function recentForTab(tabId: number | undefined): Promise<RecentPageEntry[]> {
  if (!tabId) {
    return [];
  }
  let host = tabs.get(tabId)?.host;
  if (!host) {
    try {
      const tab = await chrome.tabs.get(tabId);
      host = tab.url ? new URL(tab.url).host : '';
    } catch {
      host = '';
    }
  }
  if (!host) {
    return [];
  }
  const o = await chrome.storage.local.get(LOCAL_KEYS.recentPages);
  const all = (o[LOCAL_KEYS.recentPages] as Record<string, RecentPageEntry[]> | undefined) ?? {};
  return all[host] ?? [];
}

/** 轮盘点击跳转：当前标签页导航（tabId 不变 → 账号绑定与六平面规则无缝延续） */
export async function jumpCurrentTab(tabId: number | undefined, url: string): Promise<boolean> {
  if (!tabId || !/^https?:/i.test(url)) {
    return false;
  }
  await chrome.tabs.update(tabId, { url });
  return true;
}

export const pageMonitor = { ingestNames, recentForTab, jumpCurrentTab, registerPageMonitorListeners };
