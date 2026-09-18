import { SESSION_KEYS } from '../../shared/constants';
import { hostRelated, urlHostOf } from './host';

/**
 * 待自动登录凭证的交接通道：绑定页签打开后，内容脚本按 tabId 主动索取。
 *
 * 为什么落在 storage.session：MV3 service worker 会被回收，内存缓存不可靠；
 * storage.session 随浏览器会话存活且不落盘。
 *
 * 与账号模型解耦：并行账号（par.*）与旧会话模型（session.*）共用同一约定，
 * 故本模块不依赖任何一方——此前这段键格式与读写逻辑在 navigation.ts 与
 * parallel-session.ts 各写一份（改动一处即静默失配），现收敛为单一真源。
 *
 * ## 安全语义（2026-09-17 加固；原实现是明文口令可被投递到未绑定页面）
 *
 * 原实现有三个条件同时成立，构成一条真实可利用的凭据泄漏路径：
 *   1. `getPendingAutoLogin` **读后不删** → 60s TTL 内可重复取；
 *   2. 索取方**不校验来源**（只认 `sender.tab?.id`，不看 URL）；
 *   3. `unbindTab` **不清**该条目。
 * 而内容脚本以 `all_frames` 注入 `<all_urls>`，于是绑定页签在 60s 内导航到非绑定源
 * （或该源里的任意 iframe 索取），就能拿到该账号**明文口令**。
 *
 * 现在三条一起收口：**条目自带宿主 + 按宿主校验索取方 URL + 读后即删 + 解绑即清**。
 * 校验用 `hostRelated`（含端口身份口径，同主机不同端口视为不同站点），
 * 与页签绑定、DNR 规则同一把尺子。
 *
 * ⚠ 为什么**不**按「只信顶层 frame（`sender.frameId === 0`）」收口：
 * 实测 `auto-login.ts` 的**子 frame 也会各自索取并兜底填表**（`void requestCredentials()` 在模块
 * 末尾无条件执行，`runIframeFlow` 填自身 frame 内的密码框，跨域 SSO 登录框正靠这条路径）。
 * 按 frame 收口会把合法的跨域登录 iframe 一起打死——必须按**宿主身份**收口。
 */
const AUTO_LOGIN_TTL = 60_000;

interface PendingEntry {
  username: string;
  password: string;
  /** 下发时该页签的绑定宿主；索取方 URL 必须与它同族（缺失 = 不可校验 = 拒绝） */
  host?: string;
  at: number;
}

function pendingKey(tabId: number): string {
  return `${SESSION_KEYS.pendingAutoLogins}:${tabId}`;
}

/** 缓存待自动登录凭证（service worker 回收后不丢失）；`host` = 该页签绑定的站点宿主 */
export async function setPendingAutoLogin(
  tabId: number,
  username: string,
  password: string,
  host: string,
): Promise<void> {
  await chrome.storage.session.set({
    [pendingKey(tabId)]: { username, password, host, at: Date.now() } satisfies PendingEntry,
  });
}

function isFresh(entry: PendingEntry | undefined): entry is PendingEntry {
  return entry !== undefined && Date.now() - entry.at <= AUTO_LOGIN_TTL;
}

/**
 * 索取并**一次性消费**待登录凭证（内容脚本就绪后调用）。
 *
 * 只有同时满足「未过期」+「未绑定页签已解绑过则条目不存在」+「索取方 URL 与下发宿主同族」
 * 才返回凭据，且**无论返回与否都会删掉条目**——彻底消除「读后不删 → 60s 内重复取」这个窗口。
 *
 * @param tabId  索取方所在页签（来自 `sender.tab.id`）
 * @param url    索取方该帧的 URL（来自 `sender.url`）；取不到 host 时一律拒绝
 */
export async function claimPendingAutoLogin(
  tabId: number,
  url: string,
): Promise<{ username: string; password: string } | null> {
  const key = pendingKey(tabId);
  const stored = await chrome.storage.session.get(key);
  const entry = stored[key] as PendingEntry | undefined;
  // 先删后判：把「消费」做成无条件动作，任何早退路径都不会留下可复取的条目
  if (entry) {
    await chrome.storage.session.remove(key);
  }
  if (!isFresh(entry)) {
    return null;
  }
  const requester = urlHostOf(url);
  // 条目缺 host（旧版写入的残留）或索取方 URL 不可解析 → 不可校验，按拒绝处理
  if (!entry.host || !requester || !hostRelated(requester, entry.host)) {
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
