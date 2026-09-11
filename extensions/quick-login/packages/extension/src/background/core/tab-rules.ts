/**
 * DNR session 规则管理 —— 「多平面隔离」的网络平面 v4.1。
 *
 * 每个绑定标签页维护至多两条 session 规则：
 *  1) AUTH 规则：Authorization → set 'Bearer <token>'（token 可能为 null：未捕获前不装）；
 *  2) COOKIE 规则：Cookie 请求头 → remove（与 token 无关，绑定即装）。
 *
 * CACHE 分区平面（v3.3 引入的 `_qlck=t<tabId>`）自 v4.1 起改由 MAIN 壳页面层实现
 * （shield-main.ts 的 fetch/XHR 包装直接追加查询参数）：Chrome 的 DNR 从未实现
 * `redirect.urlTransform`（MDN 该字段为 Firefox 专属；Chromium 一直以
 * "Unexpected property: 'urlTransform'" 拒绝），且 updateSessionRules 是原子批量——
 * 该无效规则曾导致同批的 COOKIE/AUTH 规则全部被拒（四象限泄漏期间网络平面全死的根因）。
 *
 * 安装策略：逐条安装 + 单条失败降级（不再原子批量连坐）。
 * 页面存储层继续由 MAIN 壳虚拟化；目标站为无状态 JWT 设计（DESIGN.md §3）。
 */

import { hostNoPortOf, parentDomainOf } from './host';

/** AUTH 规则 id 区间 */
const AUTH_BASE = 100_000;
/** COOKIE 规则 id 区间 */
const COOKIE_BASE = 200_000;

interface RuleMeta {
  authId?: number;
  cookieId?: number;
  /** 当前 AUTH 规则的 token；null 表示未装 AUTH 规则 */
  token: string | null;
  /** 当前 COOKIE 规则的回放值；null 表示 remove（剥离）模式 */
  cookieValue: string | null;
}

const installed = new Map<number, RuleMeta>();

/** 诊断埋点（与 parallel-session 共用 storage.local['ql:diag'] 环形缓冲） */
async function diag(msg: string): Promise<void> {
  try {
    const key = 'ql:diag';
    const cur = (await chrome.storage.local.get(key))[key] as string[] | undefined;
    const next = [...(cur ?? []).slice(-59), `${new Date().toISOString().slice(11, 23)} ${msg}`];
    await chrome.storage.local.set({ [key]: next });
  } catch {
    // 埋点失败不影响业务
  }
}

let nextAuthId = AUTH_BASE;
let nextCookieId = COOKIE_BASE;

const ALL_MATCH_TYPES: chrome.declarativeNetRequest.ResourceType[] = [
  'main_frame',
  'sub_frame',
  'xmlhttprequest',
  'websocket',
  'script',
  'stylesheet',
  'image',
  'font',
  'media',
  'other',
] as chrome.declarativeNetRequest.ResourceType[];

function asRule(raw: unknown): chrome.declarativeNetRequest.Rule {
  return raw as unknown as chrome.declarativeNetRequest.Rule;
}

function buildAuthRule(ruleId: number, host: string, tabId: number, token: string): chrome.declarativeNetRequest.Rule {
  return asRule({
    id: ruleId,
    priority: 1,
    action: {
      type: 'modifyHeaders',
      requestHeaders: [{ header: 'Authorization', operation: 'set', value: `Bearer ${token}` }],
    },
    condition: {
      // 全资源类型（与 COOKIE 规则同宽，v3.13.2）：
      // - xmlhttprequest/websocket：API 与 WS 握手；
      // - sub_frame：iframe 内嵌文档（低代码平台「管理端」控制台常以 iframe 承载，
      //   无 Bearer 的子框架会被服务端当匿名拒入）；
      // - main_frame：顶层下载导航（纯 Bearer 鉴权平台的导出接口，缺头即 401）；
      // - other：`<a download>` 属性发起的下载请求在 DNR 里常归为此型（3.13.1 只补
      //   main_frame 仍 401 的教训——下载归型因发起方式而异，与 COOKIE 规则同宽才算到位）。
      // 安全边界不变：tabIds + requestDomains 双重锁死，跨域 SSO 跳转不会被附加头。
      resourceTypes: ALL_MATCH_TYPES,
      requestDomains: [hostNoPortOf(host), parentDomainOf(hostNoPortOf(host))],
      tabIds: [tabId],
    },
  });
}

/**
 * COOKIE 规则：v3.6 起支持「按账号回放」——cookieHeader 非 null 时 set（服务端始终看到
 * 一致的会话身份，对齐 SessionBox 核心机制）；null 时保持 remove（共享 jar 对绑定标签隐身）。
 */
function buildCookieRule(
  ruleId: number,
  host: string,
  tabId: number,
  cookieHeader: string | null,
): chrome.declarativeNetRequest.Rule {
  return asRule({
    id: ruleId,
    priority: 1,
    action: {
      type: 'modifyHeaders',
      requestHeaders: [
        cookieHeader
          ? { header: 'Cookie', operation: 'set', value: cookieHeader }
          : { header: 'Cookie', operation: 'remove' },
      ],
    },
    condition: {
      // 全类型覆盖：绑定标签页的出站 Cookie 完全由本规则决定，与真实 jar 无关
      resourceTypes: ALL_MATCH_TYPES,
      requestDomains: [hostNoPortOf(host), parentDomainOf(hostNoPortOf(host))],
      tabIds: [tabId],
    },
  });
}

/** 单条规则安装（返回是否成功；失败仅记录诊断，不阻断其余规则） */
async function addOne(rule: chrome.declarativeNetRequest.Rule): Promise<boolean> {
  try {
    await chrome.declarativeNetRequest.updateSessionRules({ addRules: [rule] });
    return true;
  } catch (e) {
    void diag(`addRule #${rule.id} 失败：${e instanceof Error ? e.message : String(e)}`);
    return false;
  }
}

export const tabRules = {
  /**
   * 设置/更新某绑定标签页的网络平面规则。
   * - COOKIE 规则绑定即装：cookieHeader 非 null → set（回放），变更时重建；
   *   null → remove（剥离）。
   * - token 非 null 时装/换 AUTH 规则；null 时摘除。
   * 逐条安装：单条失败不连坐其余规则。
   */
  async applyBinding(host: string, tabId: number, token: string | null, cookieHeader?: string | null): Promise<void> {
    const wanted = cookieHeader ?? null;
    const meta: RuleMeta = installed.get(tabId) ?? { token: null, cookieValue: null };

    const removes: number[] = [];

    // COOKIE 规则（首次安装，或回放值发生变化时重建）
    if (meta.cookieId === undefined || meta.cookieValue !== wanted) {
      if (meta.cookieId !== undefined) {
        removes.push(meta.cookieId);
        meta.cookieId = undefined;
      }
      const ruleId = nextCookieId++;
      if (await addOne(buildCookieRule(ruleId, host, tabId, wanted))) {
        meta.cookieId = ruleId;
        meta.cookieValue = wanted;
      }
    }

    // AUTH 规则
    if (token === null) {
      if (meta.authId !== undefined) {
        removes.push(meta.authId);
        meta.authId = undefined;
      }
      meta.token = null;
    } else if (meta.token !== token) {
      if (meta.authId !== undefined) {
        removes.push(meta.authId);
        meta.authId = undefined;
      }
      const ruleId = nextAuthId++;
      if (await addOne(buildAuthRule(ruleId, host, tabId, token))) {
        meta.authId = ruleId;
        meta.token = token;
      }
    }

    if (removes.length) {
      try {
        await chrome.declarativeNetRequest.updateSessionRules({ removeRuleIds: removes });
      } catch (e) {
        void diag(`updateRules 移除失败 ids=${removes.join(',')}：${e instanceof Error ? e.message : String(e)}`);
      }
    }
    installed.set(tabId, meta);
    void diag(
      `applyBinding tab=${tabId} token=${token ? '有' : '无'} cookie=${wanted ? `回放${wanted.length}B` : '剥离'} cookieId=${meta.cookieId ?? '-'} authId=${meta.authId ?? '-'}`,
    );
  },

  /** 标签关闭 / 解绑：摘除其全部规则 */
  async clearTab(tabId: number): Promise<void> {
    const meta = installed.get(tabId);
    if (!meta) {
      return;
    }
    const removes: number[] = [];
    if (meta.cookieId !== undefined) {
      removes.push(meta.cookieId);
    }
    if (meta.authId !== undefined) {
      removes.push(meta.authId);
    }
    try {
      if (removes.length) {
        await chrome.declarativeNetRequest.updateSessionRules({ removeRuleIds: removes });
      }
    } catch {
      // 忽略
    }
    installed.delete(tabId);
    void diag(`clearTab tab=${tabId} del=${removes.length}`);
  },

  /**
   * SW 冷启动恢复：persisted 提供 tabId → { host, token|null }，
   * 与浏览器内现存 session 规则做差集同步（孤儿清理 + 缺失重建）。
   */
  async restore(persisted: Map<number, { host: string; token: string | null; cookie?: string | null }>): Promise<void> {
    let existing: chrome.declarativeNetRequest.Rule[] = [];
    try {
      existing = await chrome.declarativeNetRequest.getSessionRules();
    } catch {
      existing = [];
    }

    // 孤儿清理：本扩展区间内、不属于当前意图集的规则
    const desiredTabs = new Set(persisted.keys());
    const removals: number[] = [];
    for (const rule of existing) {
      const inOurs = rule.id >= AUTH_BASE;
      const orphans = (rule.condition.tabIds ?? []).every((t) => !desiredTabs.has(t));
      if (inOurs && orphans) {
        removals.push(rule.id);
      }
    }
    if (removals.length) {
      try {
        await chrome.declarativeNetRequest.updateSessionRules({ removeRuleIds: removals });
      } catch {
        // 忽略
      }
    }

    nextAuthId = Math.max(
      AUTH_BASE,
      ...existing.filter((r) => r.id >= AUTH_BASE && r.id < COOKIE_BASE).map((r) => r.id + 1),
      AUTH_BASE,
    );
    nextCookieId = Math.max(
      COOKIE_BASE,
      ...existing.filter((r) => r.id >= COOKIE_BASE).map((r) => r.id + 1),
      COOKIE_BASE,
    );

    installed.clear();
    for (const [tabId, info] of persisted) {
      await this.applyBinding(info.host, tabId, info.token ?? null, info.cookie ?? null);
    }
    void diag(`tabRules.restore persisted=${persisted.size}`);
  },

  /** 诊断用内部状态快照（经 ql.diag 消息暴露给台架） */
  debugState(): Record<string, unknown> {
    return {
      installed: Array.from(installed.entries()).map(([t, m]) => ({
        t,
        authId: m.authId ?? null,
        cookieId: m.cookieId ?? null,
        token: m.token ? '有' : '无',
        cookie: m.cookieValue ? `回放${m.cookieValue.length}B` : '剥离',
      })),
      nextAuthId,
      nextCookieId,
    };
  },
};
