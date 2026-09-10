import { CONTENT_MESSAGE, LOCAL_KEYS, SESSION_KEYS } from '../../shared/constants';
import type { Scheme } from './site-auth';
import type { BridgeDownPayload, BridgeUpPayload, ParallelAccount } from '../../shared/types';
import { credentials } from './credentials';
import { setTabTitle } from '../tabs/tab-title';
import { pageMonitor } from './page-monitor';
import { parallelStore } from './parallel-store';
import { tabRules, parentDomainOf, hostNoPortOf } from './tab-rules';

/**
 * 「多平面隔离」运行时编排（纯扩展多账号并行，见 docs/BROWSER-ONLY-MULTILOGIN-RESEARCH.md §4）：
 * - 存储平面：MAIN 壳把绑定标签页的 localStorage / document.cookie 重定向到账号命名空间；
 *   桥接脚本上报 `__auth_token__` 等键的写入 → 本模块捕获 token 快照。
 * - 网络平面：每绑定标签两条 DNR 规则——Authorization 改写 + 出站 Cookie 头剥离（v3 新增，
 *   封堵「真实 jar = 最后登录者」的串号通道）。
 * - 授权健康：host 缺少浏览器授权或被手动停用时，网络平面整体关闭并在 UI 显著提示。
 */

interface ParBinding {
  accountId: string;
  host: string;
  /** 亲子继承页签（v3.9.6 window.open/_blank）——进入登录页时自动转原始页签（v3.10.4） */
  adopted?: boolean;
}

interface TokenSnapshot {
  token?: string;
  authUser?: string;
  deviceFp?: string;
  /**
   * 登录时点的站内 Cookie 快照（含 HttpOnly，经 chrome.cookies 读取；身份类黑名单除外，
   * 见 IDENTITY_COOKIE_BLACKLIST）。用于 DNR Cookie 头「按账号回放」（v3.6 对齐 SessionBox
   * 核心机制）：绑定标签页的出站请求不再依赖共享真实 jar，服务端始终看到一致会话身份。
   * 仅在该账号首次捕获 token（即登录刚完成、jar 尚未被后续登录污染）时采集。
   */
  cookies?: { name: string; value: string }[];
}

const bindings = new Map<number, ParBinding>();
/** 亲子继承候选（v3.13 加固）：URL 未确认授权前不发种子/不装规则；
 *  expires 超时未导航（如停留在 about:blank）则自动放弃并通知壳回直通。 */
const pendingAdoptions = new Map<number, { accountId: string; host: string; expires: number }>();
const PENDING_ADOPT_TTL = 30_000;
const tokens = new Map<string, TokenSnapshot>();
/** 授权健康缓存：host → 是否可执行（已授权且未被手动停用） */
const enforcement = new Map<string, boolean>();
/** MAIN 壳 Cookie 袋的命名空间键名（与 shield-main.ts 的 COOKIE_BAG_KEY 一致） */
const COOKIE_BAG_KEY = '__ql_cookies__';

/** 诊断埋点：写入 storage.local['ql:diag']（环形 60 条），供 E2E 台架经扩展页读取 */
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

/** 登录失败现场取证（v3.12.2）：结构化事件写入 storage.local['ql:forensics']（环形 120 条）。
 *  与 diag（人读文本）并行；管理页「导出诊断包」一键取走。 */
export async function forensics(ev: string, detail: Record<string, unknown> = {}): Promise<void> {
  try {
    const key = LOCAL_KEYS.forensics;
    const cur = (await chrome.storage.local.get(key))[key] as
      | { t: number; ev: string; [k: string]: unknown }[]
      | undefined;
    const next = [
      ...(cur ?? []).slice(-119),
      { t: Date.now(), ev, ...detail },
    ];
    await chrome.storage.local.set({ [key]: next });
  } catch {
    // 取证失败不影响业务
  }
}

async function readBlockedHosts(): Promise<Set<string>> {
  const stored = await chrome.storage.local.get(LOCAL_KEYS.blockedHosts);
  return new Set((stored[LOCAL_KEYS.blockedHosts] as string[] | undefined) ?? []);
}

/** 检查某 host 的网络平面是否可执行（带缓存的授权 + 封锁名单判定） */
async function isEnforceable(host: string): Promise<boolean> {
  const cached = enforcement.get(host);
  if (cached !== undefined) {
    void diag(`isEnforceable(${host}) → 缓存 ${cached}`);
    return cached;
  }
  const blocked = await readBlockedHosts();
  if (blocked.has(host)) {
    enforcement.set(host, false);
    void diag(`isEnforceable(${host}) → false（本地停用名单）`);
    return false;
  }
  let granted = false;
  try {
    granted = await chrome.permissions.contains({ origins: [`*://${host}/*`] });
  } catch {
    granted = false;
  }
  // 更宽泛的通配授权也算可用
  if (!granted) {
    try {
      granted = await chrome.permissions.contains({ origins: ['*://*/*'] });
    } catch {
      granted = false;
    }
  }
  enforcement.set(host, granted);
  void diag(`isEnforceable(${host}) → ${granted}（permissions.contains 实查）`);
  return granted;
}

/** 强制刷新授权缓存（授权增撤后调用） */
export function invalidateEnforcementCache(): void {
  enforcement.clear();
}

/** 预热授权健康缓存（par.list 调用，供 statusOf 同步读取；isEnforceable 有缓存，稳态开销近零） */
export async function warmEnforcementCache(hosts: string[]): Promise<void> {
  await Promise.all([...new Set(hosts)].map((h) => isEnforceable(h).catch(() => undefined)));
}

async function readState(): Promise<void> {
  const [bindStored, tokenStored] = await Promise.all([
    chrome.storage.session.get(SESSION_KEYS.parTabBindings),
    chrome.storage.session.get(SESSION_KEYS.parTokens),
  ]);
  bindings.clear();
  const bindMap = bindStored[SESSION_KEYS.parTabBindings] as Record<string, ParBinding> | undefined;
  if (bindMap) {
    for (const [tabId, b] of Object.entries(bindMap)) {
      bindings.set(Number(tabId), b);
    }
  }
  tokens.clear();
  const tokenMap = tokenStored[SESSION_KEYS.parTokens] as Record<string, TokenSnapshot> | undefined;
  if (tokenMap) {
    for (const [id, t] of Object.entries(tokenMap)) {
      tokens.set(id, t);
    }
  }
}

async function persistBindings(): Promise<void> {
  const map: Record<string, ParBinding> = {};
  for (const [tabId, b] of bindings) {
    map[String(tabId)] = b;
  }
  await chrome.storage.session.set({ [SESSION_KEYS.parTabBindings]: map });
}

async function persistTokens(): Promise<void> {
  const map: Record<string, TokenSnapshot> = {};
  for (const [id, t] of tokens) {
    map[id] = t;
  }
  await chrome.storage.session.set({ [SESSION_KEYS.parTokens]: map });
}

/* ---------------- 标题与待登录凭证（复用既有内容脚本协议） ---------------- */

async function applyTitle(tabId: number, tabName: string): Promise<void> {
  await setTabTitle(tabId, tabName);
  try {
    await chrome.tabs.sendMessage(tabId, { type: CONTENT_MESSAGE.setTitle, alias: tabName });
  } catch {
    // 内容脚本未就绪：标题已权威写入；后续 onUpdated 会再推
  }
}

/** 与 navigation.ts 相同约定的待登录凭证缓存（auto-login 内容脚本按 tabId 拉取） */
async function setPendingAutoLogin(tabId: number, username: string, password: string): Promise<void> {
  await chrome.storage.session.set({
    [`${SESSION_KEYS.pendingAutoLogins}:${tabId}`]: { username, password, at: Date.now() },
  });
}

function boundTabsOf(accountId: string): number[] {
  const out: number[] = [];
  for (const [tabId, b] of bindings) {
    if (b.accountId === accountId) {
      out.push(tabId);
    }
  }
  return out;
}

/* ---------------- token 捕获 → DNR 规则同步（网络平面写入点） ---------------- */

async function captureToken(accountId: string, host: string, rawToken: string): Promise<void> {
  const token = rawToken.startsWith('Bearer ') ? rawToken.slice(7).trim() : rawToken.trim();
  if (!token || !/^[\w-]+\.[\w-]+\.[\w-]*$/.test(token)) {
    return; // 仅接受形如 JWT 的值，避免把页面噪声写进规则
  }
  const snap = tokens.get(accountId) ?? {};
  if (snap.token === token) {
    return; // 去重
  }
  // 异账号护栏（v3.10.3/3.10.4）：绑定页签内登录了另一个账号 → 不更新本账号
  // 快照（authHeader 嗅探通道同样拦截），由 storageWrite 通道的叛逃处置统一接管。
  // v3.10.4：改用「剥离时效字段后的整个 payload」比对——不依赖具体 claim 名
  //（真实平台 token 的身份字段名各异，逐 claim 提取会全部落空导致护栏失效）
  if (snap.token) {
    const oldId = jwtStableIdentity(snap.token);
    const newId = jwtStableIdentity(token);
    if (oldId && newId && oldId !== newId) {
      void diag(`captureToken(${accountId}) 拦截异账号 token（身份主体变更）`);
      return;
    }
  }
  const isFirstCapture = !snap.token; // 首次捕获 = 登录刚完成：此刻真实 jar 即该账号会话
  snap.token = token;
  tokens.set(accountId, snap);
  await persistTokens();
  void forensics('token-captured', {
    accountId,
    first: isFirstCapture,
    tokenLen: token.length,
    cookieCount: snap.cookies?.length ?? 0,
  });
  // 快照触发必须在 captureToken 内部：token 首捕可能来自 authHeader 嗅探（早于
  // storageWrite 事件），两条通道都必须覆盖，否则错过登录时点（v3.6.1 修复）
  if (isFirstCapture) {
    await snapshotLoginCookies(accountId, host);
  }
  await syncAccountRules(accountId, host);
}

/* ---------------- 身份叛逃检测与处置（v3.10.3） ----------------
 * 场景：用户在轮盘登录账号 A（绑定页签），随后新开页签（低代码平台 window.open 或
 * target=_blank 带 opener → 被 v3.9.6 亲子继承绑定为 A）手输网址登录账号 B——
 * B 的登录发生在 A 的绑定/命名空间内：token/user 写进 A 的命名空间（持久化，关页签
 * 不清），captureToken 还会把 A 的快照 token 换成 B 的 → 双向身份污染（A 页显示 B 的
 * 名字/A 的权限等乱象），且关光页签重开也无法恢复。
 * 原则（与 3.10.2 一致）：原始登录与快速登录互不冲突——用户在页签里登录了别的账号，
 * 说明该页签被用户「征用」为原始浏览器：转为原始页签（回滚其命名空间写入 → 解绑 →
 * 重载），B 的会话走真实 jar 天然成立；账号 A 的快照与其它页签零污染。
 */

/** 解码 JWT 载荷并剥离时效性字段（exp/iat/nbf/jti/sid 等），返回稳定载荷的规范化串。
 *  与 jwtIdentity 的逐 claim 提取不同，本函数不依赖任何具体字段名——
 *  同一账号的 token 轮换（仅时效字段变化）稳定载荷相同，异账号必然不同。
 *  v3.12.3：**`authcode` 加入时效集**——实测平台（Akso eGMP）token 载荷含每次签发
 *  都重新生成的 `AuthCode`（GUID），不在排除集时同账号的「登录轮换」会被身份护栏
 *  误判为异账号 token 而拒绝记录 → 快照卡死旧 token → API 全 401 → 反复登录失败
 *  （用户实测「退出→关页→再快捷登录登不上，第二次重开才好」的根因，现场实锤）。 */
function jwtStableIdentity(token: string): string | null {
  try {
    const part = token.split('.')[1];
    if (!part) {
      return null;
    }
    const b64 = part.replace(/-/g, '+').replace(/_/g, '/');
    const json = atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4));
    const claims = JSON.parse(json) as Record<string, unknown>;
    if (typeof claims !== 'object' || claims === null) {
      return null;
    }
    const VOLATILE = new Set([
      'exp',
      'iat',
      'nbf',
      'jti',
      'sid',
      'auth_time',
      'loginTime',
      'timestamp',
      'nonce',
      // 每次签发都变化的平台自定义字段（实测 AuthCode = 每次登录/轮换重新生成的 GUID）
      'authcode',
      'authCode',
      'AuthCode',
    ]);
    const stable: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(claims)) {
      if (!VOLATILE.has(k)) {
        stable[k] = v;
      }
    }
    return sortedStringify(stable);
  } catch {
    return null;
  }
}

/** 键序无关的规范化序列化（载荷键序因签发实现而异，需消除其影响） */
function sortedStringify(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map((v) => sortedStringify(v)).join(',')}]`;
  }
  if (typeof value === 'object' && value !== null) {
    const obj = value as Record<string, unknown>;
    return `{${Object.keys(obj).sort().map((k) => `${JSON.stringify(k)}:${sortedStringify(obj[k])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

/** 提取 __auth_user__ 值中的用户名主体；**不可解析/无标准字段时返回 null**（护栏放行）。
 *  v3.12.0 防误判：旧实现返回 JSON 原文——原文里的时间戳/字段序差异会让同账号的
 *  两次写入被判「主体变更」→ defectTabToRaw 解绑重载 → 登录成功即被踢回登录页。 */
function authUserIdentity(value: string): string | null {
  try {
    const obj = JSON.parse(value) as Record<string, unknown>;
    for (const key of ['username', 'userName', 'loginName', 'account', 'name', 'mobile', 'phone', 'email', 'uid', 'userId', 'id']) {
      const v = obj[key];
      if (typeof v === 'string' && v) {
        return v;
      }
      if (typeof v === 'number') {
        return String(v);
      }
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * 身份叛逃处置：把「被用户用于登录另一账号」的绑定页签转回原始页签。
 * 1. 回滚该页签本页会话对命名空间的全部写入（journalRollback，含 Cookie 袋）；
 * 2. 清扫该账号命名空间的 IDB/CacheStorage（叛逃会话写入的他人缓存）；
 * 3. 治愈账号快照（清除可能被叛逃写入的 authUser；token 由护栏保证未被替换）；
 * 4. 给该账号其余绑定页签重灌种子（覆盖种子键，防御性）；
 * 5. 解绑 + 重载 → 页签成为原始页签，B 的会话在真实 jar 中原生成立。
 */
async function defectTabToRaw(tabId: number, accountId: string, host: string, reason: string): Promise<void> {
  void diag(`defect(${tabId}) 账号=${accountId} @${host} 身份叛逃（${reason}）→ 转为原始页签`);
  void forensics('defect', { tabId, accountId, reason });
  await pushDown(tabId, { op: 'journalRollback' });
  await pushDown(tabId, { op: 'nsWipeShared' });
  const snap = tokens.get(accountId);
  if (snap) {
    delete snap.authUser;
    tokens.set(accountId, snap);
    await persistTokens();
  }
  for (const other of boundTabsOf(accountId)) {
    if (other !== tabId) {
      await pushBind(other); // 防御性重灌：种子键恢复为本账号值
    }
  }
  await parallelSession.unbindTab(tabId);
  void chrome.tabs.reload(tabId).catch(() => undefined);
}

/**
 * 身份类 Cookie 黑名单：这些名字由平台 JS 写入真实 jar（普通/未接管页签无虚拟化保护），
 * 值 = 「jar 里最后写它的那个账号」。快照若包含它们，回放就会把别人的身份带给本账号
 * 的请求（3.7.2 修复：真实环境复现「普通用户获得管理员」——普通页签登录态残留 jar，
 * 被下一次任意账号的登录时点快照打包）。回放只需 WAF 会话对等非身份 Cookie。
 */
const IDENTITY_COOKIE_BLACKLIST = new Set(['__auth_token__', '__auth_user__', '__device_fp__']);

/* ---------------- 会话卫生（v3.10.2）：真实 jar 永不留存扩展账号的会话 Cookie ----------------
 * 冲突机制：绑定页签登录时响应 Set-Cookie 不经拦截直接落入真实 jar；此后用户 Ctrl+T 的
 * 原始页签出站时带着的就是扩展账号的会话 Cookie——站点按会话轮换逻辑「新登录作废旧会话」，
 * 快速登录的账号被顶掉（原始登录与快速登录互相冲突）。
 * 对策：扩展账号页签完全依赖快照回放（与真实 jar 无关），因此 jar 中的扩展会话 Cookie 可
 * 安全移除——原始页签从此天然匿名，原始登录 = 全新会话，互不顶号。
 * 三层防线：
 *  1. preJar 基线：open() 建页签前记录 jar；
 *  2. 差集清扫：快照首捕时移除「本次登录新写入」的 Cookie（不动登录前已存在的，如原始会话）；
 *  3. onChanged 持续驱逐：凡是 (name,value) 命中任一账号快照对（或 __auth_token__ 命中任一
 *     账号 token）的写 jar 行为，立即移除——覆盖后续请求重新 Set-Cookie 的重入。
 */

interface JarCookie {
  name: string;
  value: string;
  domain: string;
  path: string;
  secure: boolean;
}

/** 登录前 jar 基线（按账号；SW 重启丢失则跳过清扫，保守不误杀） */
const preJarMap = new Map<string, JarCookie[]>();

/** 账号站点的协议（v3.10.9）：缺省 = https（兼容存量账号）。打开 URL 与 Cookie 查询跟随 */
async function schemeOfAccount(accountId: string): Promise<Scheme> {
  try {
    const account = await parallelStore.get(accountId);
    return account.scheme ?? 'https';
  } catch {
    return 'https';
  }
}

async function capturePreJar(accountId: string, host: string): Promise<void> {
  try {
    const scheme = await schemeOfAccount(accountId);
    preJarMap.set(accountId, (await chrome.cookies.getAll({ url: `${scheme}://${host}/` })) as JarCookie[]);
  } catch {
    preJarMap.delete(accountId);
  }
}

function cookieUrl(c: { domain: string; path: string; secure: boolean }): string {
  const host = c.domain.startsWith('.') ? c.domain.slice(1) : c.domain;
  return `${c.secure ? 'https' : 'http'}://${host}${c.path || '/'}`;
}

/** 从真实 jar 移除一枚 Cookie（幂等；失败静默——可能已被站点的过期指令处理） */
async function removeJarCookie(c: { name: string; domain: string; path: string; secure: boolean }): Promise<void> {
  try {
    await chrome.cookies.remove({ url: cookieUrl(c), name: c.name });
  } catch {
    // 幂等目标，失败忽略
  }
}

/** 快照首捕后按差集清扫：只移除「本次登录新写入」的 Cookie，保留登录前已存在的（如原始会话）。
 *  v3.11.1 归属门控：候选还必须是「绑定页签写入」（webRequest 归属）——同账号原生登录
 *  写入的同值 Cookie 属于原生会话，受根本原则保护，不清扫。 */
async function sweepLoginCookiesFromJar(accountId: string, host: string, captured: Array<{ name: string; value: string }>): Promise<void> {
  const pre = preJarMap.get(accountId);
  if (!pre) {
    void diag(`sweep(${accountId}) 跳过：无登录前基线（SW 重启）——依赖 onChanged 驱逐兜底`);
    return;
  }
  const scheme = await schemeOfAccount(accountId);
  const preKeys = new Set(pre.map((c) => `${c.name}|${c.value}`));
  const jarNow = (await chrome.cookies.getAll({ url: `${scheme}://${host}/` }).catch(() => [])) as JarCookie[];
  let removed = 0;
  let protectedCount = 0;
  for (const c of jarNow) {
    // 差集成员：在 jar 中、被快照捕获、但登录前不存在 → 本会话写入的扩展 Cookie
    if (!preKeys.has(`${c.name}|${c.value}`) && captured.some((k) => k.name === c.name && k.value === c.value)) {
      const attr = cookieAttribution.get(`${c.name}|${c.value}`);
      if (!attr || !attr.bound) {
        protectedCount++;
        continue; // 非绑定页签写入：原生会话，受根本原则保护
      }
      await removeJarCookie(c);
      removed++;
    }
  }
  preJarMap.delete(accountId);
  void diag(`sweep(${accountId}) 差集清扫 ${removed} 枚（jar 现存 ${jarNow.length}，原生保护 ${protectedCount}）`);
}

/* ---------------- 写入者归属（v3.11.1 根本原则）----------------
 * 根本原则：扩展只能影响扩展打开的网页，不得以规则/能力影响原有网页。会话卫生
 * （驱逐/清扫）此前不区分写入者——同账号「原生登录」（原始页签手输登录）写入真实
 * jar 的会话 Cookie 会与快照对撞而被驱逐/清扫 → 扩展破坏原生会话。
 * 对策：观察型 webRequest 记录每条 Set-Cookie 的写入者（所在响应的 tabId 是否绑定）；
 * 驱逐/清扫只作用于「绑定页签写入」的 Cookie。原生页签的网络写入归属为 unbound；
 * 原生页签的 JS document.cookie 写入不产生网络事件（无归属）——同样保留。 */
const cookieAttribution = new Map<string, { bound: boolean; at: number }>();
const ATTRIBUTION_LIMIT = 800;

function trackCookieAttribution(details: { tabId: number; responseHeaders?: { name: string; value?: string }[] }): void {
  const sets = (details.responseHeaders ?? []).filter((h) => h.name.toLowerCase() === 'set-cookie');
  if (!sets.length) {
    return;
  }
  const bound = bindings.has(details.tabId);
  for (const h of sets) {
    const parsed = parseSetCookie(h.value ?? '');
    if (!parsed || parsed.remove) {
      continue; // 作废指令不是「写入」
    }
    cookieAttribution.set(`${parsed.name}|${parsed.value}`, { bound, at: Date.now() });
  }
  while (cookieAttribution.size > ATTRIBUTION_LIMIT) {
    const oldest = cookieAttribution.keys().next().value;
    if (oldest === undefined) {
      break;
    }
    cookieAttribution.delete(oldest);
  }
}

/** 登录态终结（v3.12.0）：账号最后一个绑定页签关闭时调用。
 *  快照清空（token/Cookie/authUser）；jar 中由绑定页签写入的该账号 Cookie 按
 *  attribution 门控清扫（原生页签写入的不动）。下次打开 = 干净登录页 + 自动填表。 */
async function terminateLoginState(accountId: string, host: string): Promise<void> {
  const snap = tokens.get(accountId);
  if (!snap || (!snap.token && !snap.cookies?.length && !snap.authUser)) {
    return; // 无登录态可终结
  }
  void forensics('terminate', {
    accountId,
    hadToken: Boolean(snap.token),
    cookieCount: snap.cookies?.length ?? 0,
  });
  void diag(
    `terminateLoginState(${accountId}) 最后页签关闭：终结登录态（token=${snap.token ? '有' : '无'} cookie=${snap.cookies?.length ?? 0}）`,
  );
  try {
    const scheme = await schemeOfAccount(accountId).catch(() => 'https' as const);
    const jarNow = (await chrome.cookies.getAll({ url: `${scheme}://${hostNoPortOf(host)}/` }).catch(() => [])) as JarCookie[];
    for (const c of jarNow) {
      if (IDENTITY_COOKIE_BLACKLIST.has(c.name)) {
        continue;
      }
      const attr = cookieAttribution.get(`${c.name}|${c.value}`);
      if (attr?.bound && (snap.cookies ?? []).some((k) => k.name === c.name && k.value === c.value)) {
        await removeJarCookie(c);
      }
    }
  } catch {
    // 清扫失败不影响快照终结
  }
  delete snap.token;
  delete snap.cookies;
  delete snap.authUser;
  tokens.set(accountId, snap);
  await persistTokens();
}

/** onChanged 持续驱逐：命中任一账号快照对（或身份 token）的写 jar 行为立即移除 */
async function evictJarCookie(change: chrome.cookies.CookieChangeInfo): Promise<void> {
  const c = change.cookie;
  if (!c.value) {
    return; // 移除事件本身
  }
  const key = `${c.name}|${c.value}`;
  const known = new Set<string>();
  for (const snap of tokens.values()) {
    for (const k of snap.cookies ?? []) {
      known.add(`${k.name}|${k.value}`);
    }
    if (snap.token) {
      known.add(`__auth_token__|${snap.token}`);
    }
  }
  if (!known.has(key)) {
    return;
  }
  // v3.11.1 根本原则归属门控：仅驱逐「绑定页签写入」的 Cookie。写入者归属来自
  // webRequest 响应观察（Set-Cookie 所在响应的 tabId 是否绑定）——同账号原生登录、
  // 原始页签的任何写 jar 行为一律保留（扩展不得影响原有网页）。
  const attr = cookieAttribution.get(key);
  if (!attr || !attr.bound) {
    void diag(`evict 跳过 ${c.name}（写入者非绑定页签——原生会话受保护）`);
    return;
  }
  await removeJarCookie(c);
  void diag(`evict jar cookie ${c.name}（命中账号快照·绑定页签写入）@ ${c.domain}`);
}

/** 登录时点快照该账号的站内 Cookie（含 HttpOnly，剔除身份类黑名单）进账号档案。
 *  v3.10.6：与登录前暂存的页内袋值合并（页内 JS 在 token 捕获前写入的 Cookie 不丢）。 */
async function snapshotLoginCookies(accountId: string, host: string): Promise<void> {
  const snap = tokens.get(accountId);
  if (!snap?.token) {
    return;
  }
  try {
    const scheme = await schemeOfAccount(accountId);
    const list = await chrome.cookies.getAll({ url: `${scheme}://${host}/` });
    const before = list.length;
    const jarCookies = list.filter((c) => !IDENTITY_COOKIE_BLACKLIST.has(c.name));
    if (snap.cookies?.length) {
      // 合并：登录时点 jar 全量为权威，登录前页内写入的暂存袋值保留补充（jar 同名以 jar 为准）
      const merged = new Map(snap.cookies.map((c) => [c.name, c.value]));
      for (const c of jarCookies) {
        merged.set(c.name, c.value);
      }
      snap.cookies = [...merged.entries()].map(([name, value]) => ({ name, value }));
    } else {
      snap.cookies = jarCookies.map((c) => ({ name: c.name, value: c.value }));
    }
    tokens.set(accountId, snap);
    await persistTokens();
    void diag(`snapshotLoginCookies(${accountId}) 快照 ${snap.cookies.length} 条（jar 共 ${before}，剔身份类 ${before - jarCookies.length}）`);
    // 会话卫生：把本次登录写入真实 jar 的差集 Cookie 移除（原始页签从此匿名登录，不顶号）
    await sweepLoginCookiesFromJar(accountId, host, snap.cookies);
  } catch (e) {
    void diag(`snapshotLoginCookies(${accountId}) 失败：${e instanceof Error ? e.message : String(e)}`);
  }
}

/* ---------------- 快照动态化（v3.10.6）：登录后 Cookie 实时进回放 ----------------
 * 静态快照的缺陷：登录后服务端 Set-Cookie（会话轮换/下载票据）与页内 JS 写入
 * （袋虚拟化，jar 不可见）都进不了回放——绑定页签下载等「依赖登录后凭据」的请求
 * 被服务端当旧会话拒绝（虚拟环境无法下载、正常环境可以）。两条实时通道：
 *  1. webRequest 响应捕获：按 tabId 归属账号，Set-Cookie 增量并入快照 + 规则热更新；
 *  2. 袋回流：MAIN 壳的 document.cookie 写入（袋全量）经桥上报并入快照。
 * 合并引擎：变化才 persist + syncAccountRules（applyBinding 值不变不重建规则）。 */

/** 快照合并：updates 覆盖/新增，removals 删除（服务端作废指令）。返回是否有变化 */
async function mergeCookieSnapshot(
  accountId: string,
  host: string,
  updates: { name: string; value: string }[],
  removals: Set<string>,
): Promise<boolean> {
  // v3.10.8：移除「未登录不维护快照」的 token 门禁。登出清空快照后进入登录前窗口，
  // 站点下发的 WAF 会话/防机器人 Cookie 若被丢弃，登录 POST 将以「完全无 Cookie」的
  // 裸态发出而被 WAF/服务端拒绝（快捷登录登出后失败的根因）。登录前窗口同样维护
  // 快照（身份键过滤已有，无跨账号风险），使登录请求与原生浏览器等价。
  const snap = tokens.get(accountId) ?? {};
  const next = new Map((snap.cookies ?? []).map((c) => [c.name, c.value]));
  let changed = false;
  for (const name of removals) {
    if (IDENTITY_COOKIE_BLACKLIST.has(name) || !next.has(name)) {
      continue;
    }
    next.delete(name);
    changed = true;
  }
  for (const u of updates) {
    if (IDENTITY_COOKIE_BLACKLIST.has(u.name)) {
      continue;
    }
    if (next.get(u.name) !== u.value) {
      next.set(u.name, u.value);
      changed = true;
    }
  }
  if (!changed) {
    return false;
  }
  snap.cookies = [...next.entries()].map(([name, value]) => ({ name, value }));
  tokens.set(accountId, snap);
  await persistTokens();
  void diag(`mergeCookieSnapshot(${accountId}) +${updates.length} -${removals.size} → ${snap.cookies.length} 条${snap.token ? '' : '（登录前窗口）'}，热更新回放`);
  await syncAccountRules(accountId, host);
  return true;
}

/** 解析 Set-Cookie 头：取首段 k=v；Max-Age<=0 或 expires 过期 → 服务端作废指令 */
function parseSetCookie(raw: string): { name: string; value: string; remove: boolean } | null {
  const first = raw.split(';')[0] ?? '';
  const eq = first.indexOf('=');
  if (eq <= 0) {
    return null;
  }
  const name = first.slice(0, eq).trim();
  const value = first.slice(eq + 1).trim();
  if (!name) {
    return null;
  }
  let remove = false;
  for (const attr of raw.split(';').slice(1)) {
    const [k, v] = attr.split('=');
    const key = (k ?? '').trim().toLowerCase();
    if (key === 'max-age' && Number.parseInt(v?.trim() ?? '', 10) <= 0) {
      remove = true;
    } else if (key === 'expires') {
      const t = Date.parse((v ?? '').trim());
      if (!Number.isNaN(t) && t <= Date.now()) {
        remove = true;
      }
    }
  }
  return { name, value, remove };
}

/** v3.13.2 下载/请求失败取证：绑定页签的 401/403/5xx 响应全量留痕（URL/host/归型/覆盖域），
 *  让「下载失败类」问题在下一次诊断包里直接可读，不再依赖症状猜测。 */
async function reportFailureStatus(details: {
  tabId: number;
  url: string;
  statusCode?: number;
  type?: string;
}): Promise<void> {
  const status = details.statusCode ?? 0;
  if (status < 400) {
    return;
  }
  const binding = details.tabId > 0 ? bindings.get(details.tabId) : undefined;
  if (!binding) {
    return; // 未绑定页签的失败与本扩展无关
  }
  let urlHostname = '';
  try {
    urlHostname = new URL(details.url).hostname;
  } catch {
    return;
  }
  const bindHostname = hostNoPortOf(binding.host);
  const parent = parentDomainOf(bindHostname);
  const covered = urlHostname === bindHostname || urlHostname.endsWith(`.${parent}`);
  // 全量记录：401/403 任何类型；其余 4xx/5xx 仅主框架/下载类（避免 favicon 404 噪声）
  if (status === 401 || status === 403 || details.type === 'main_frame' || details.type === 'other') {
    void diag(
      `⚠ 页签请求失败 status=${status} type=${details.type ?? '?'} covered=${covered} host=${urlHostname} url=${details.url.slice(0, 180)}`,
    );
  }
}

/** 绑定页签收到的响应 Set-Cookie → 归属账号并入快照（观察型 webRequest，不改写） */async function captureResponseCookies(
  details: { tabId: number; url: string; responseHeaders?: { name: string; value?: string }[] },
): Promise<void> {
  let accountId: string | undefined;
  let bindHost: string | undefined;
  if (details.tabId > 0) {
    const binding = bindings.get(details.tabId);
    accountId = binding?.accountId;
    bindHost = binding?.host;
  } else if (details.tabId <= 0) {
    // v3.10.8 归属优化：无页签请求（Service Worker 内 fetch / 下载管理器重试 / 预取）
    // 此前直接丢弃——下载票据恰好经此通道获得时即「下载被拒」。仅当当前恰好只有
    // 一个已绑定账号时才可唯一归属；多账号并存仍无法判定，维持丢弃。
    const unique = new Set([...bindings.values()].map((b) => b.accountId));
    if (unique.size === 1) {
      const only = [...bindings.values()][0];
      accountId = only.accountId;
      bindHost = only.host;
      void diag(`captureResponseCookies：无页签响应(${new URL(details.url, 'https://x').host})归属唯一绑定账号 ${accountId}`);
    } else {
      return;
    }
  }
  if (!accountId || !bindHost) {
    return;
  }
  let urlHostname = '';
  try {
    urlHostname = new URL(details.url).hostname;
  } catch {
    return;
  }
  const bindHostname = hostNoPortOf(bindHost);
  const parent = parentDomainOf(bindHostname);
  if (urlHostname !== bindHostname && !urlHostname.endsWith(`.${parent}`)) {
    // 覆盖域之外：Cookie 回放与剥离均不作用。下载类响应在此域被拒时即为根因——
    // 诊断记录（现场可定位）；不自动扩展覆盖域，避免向第三方域回放账号 Cookie。
    const headers = details.responseHeaders ?? [];
    const cd = headers.find((h) => h.name.toLowerCase() === 'content-disposition');
    const ct = headers.find((h) => h.name.toLowerCase() === 'content-type');
    if (cd && /attachment/i.test(cd.value ?? '')) {
      void diag(`⚠ 绑定页签的下载响应来自未覆盖域 ${urlHostname}（attachment）——账号 Cookie 不回放到该域，需平台侧确认或扩展覆盖策略`);
    } else if (ct && /octet-stream|application\/pdf/i.test(ct.value ?? '')) {
      void diag(`ℹ 绑定页签的文件类响应来自未覆盖域 ${urlHostname}（${ct.value?.slice(0, 40)}）`);
    }
    return;
  }
  const sets = (details.responseHeaders ?? []).filter((h) => h.name.toLowerCase() === 'set-cookie');
  if (!sets.length) {
    return;
  }
  const updates: { name: string; value: string }[] = [];
  const removals = new Set<string>();
  for (const h of sets) {
    const parsed = parseSetCookie(h.value ?? '');
    if (!parsed || IDENTITY_COOKIE_BLACKLIST.has(parsed.name)) {
      continue;
    }
    if (parsed.remove) {
      removals.add(parsed.name);
    } else {
      removals.delete(parsed.name);
      updates.push({ name: parsed.name, value: parsed.value });
    }
  }
  if (!updates.length && !removals.size) {
    return;
  }
  try {
    await mergeCookieSnapshot(accountId, bindHost, updates, removals);
  } catch (e) {
    void diag(`captureResponseCookies(tab=${details.tabId}) 异常：${e instanceof Error ? e.message : String(e)}`);
  }
}

/** 处理 MAIN 壳上报的 Cookie 袋全量（页内 document.cookie 写入的虚拟视图） */
async function mergeBagIntoSnapshot(accountId: string, host: string, bag: Record<string, string>): Promise<void> {
  const updates: { name: string; value: string }[] = [];
  for (const [name, value] of Object.entries(bag)) {
    if (name === COOKIE_BAG_KEY || IDENTITY_COOKIE_BLACKLIST.has(name) || !value) {
      continue;
    }
    updates.push({ name, value });
  }
  if (!updates.length) {
    return;
  }
  // v3.10.8：pre-token 分支已并入 mergeCookieSnapshot（登录前窗口同样热更新回放）
  await mergeCookieSnapshot(accountId, host, updates, new Set());
}

function cookieHeaderOf(accountId: string): string | null {
  const cs = tokens.get(accountId)?.cookies;
  if (!cs || !cs.length) {
    return null;
  }
  // 回放侧再过滤一次：3.7.2 之前保存的存量快照可能已含身份类 Cookie（jar 残留），
  // 无需用户重新登录即生效
  const filtered = cs.filter((c) => !IDENTITY_COOKIE_BLACKLIST.has(c.name));
  if (!filtered.length) {
    return null;
  }
  return filtered.map((c) => `${c.name}=${c.value}`).join('; ');
}

/** 把某账号当前 token 同步到其全部绑定标签页的规则（受授权健康门控） */
async function syncAccountRules(accountId: string, host: string): Promise<void> {
  const bound = boundTabsOf(accountId);
  void diag(`syncAccountRules(${accountId}) 绑定标签=${bound.join(',') || '无'}`);
  if (!(await isEnforceable(host))) {
    void diag(`syncAccountRules(${accountId}) 跳过：授权不可执行`);
    return; // 授权缺失/停用：不装规则，UI 通过 enforcementOff 提示
  }
  const token = tokens.get(accountId)?.token ?? null;
  const cookieHeader = cookieHeaderOf(accountId);
  try {
    await Promise.all(bound.map((tabId) => tabRules.applyBinding(host, tabId, token, cookieHeader)));
    void diag(
      `syncAccountRules(${accountId}) 完成：applyBinding ×${bound.length}（token=${token ? '有' : '无'} cookie=${cookieHeader ? `${cookieHeader.length}B` : '剥离'}）`,
    );
  } catch (e) {
    void diag(`syncAccountRules(${accountId}) 异常：${e instanceof Error ? e.message : String(e)}`);
    throw e;
  }
}

/* ---------------- 绑定生命周期 ---------------- */

export const parallelSession = {
  /** 打开（或新建）账号标签页并完成绑定 */
  async open(accountId: string, forceNewTab = false): Promise<{ tabId: number; reused: boolean }> {
    void diag(`open(${accountId}) 入口`);
    const account = await parallelStore.get(accountId);
    let tabId: number | null = null;

    if (!forceNewTab) {
      const existing = boundTabsOf(accountId)[0];
      if (existing !== undefined && (await tabStillAlive(existing))) {
        tabId = existing;
      }
    }
    void forensics('open', {
      accountId,
      forceNewTab,
      reused: tabId !== null,
      hasCredentials: Boolean(account.credentials),
      box: account.box ?? null,
    });

    if (tabId === null) {
      // 登录前基线：记录打开时刻的真实 jar，快照首捕时按差集清扫（v3.10.2 会话卫生）
      await capturePreJar(account.id, account.siteHost);
      // v3.12.0 登录态生命周期跟随页签：无活绑定页签 = 登录态已终结（最后页签关闭时
      // 快照已清）——此刻残留的 token/Cookie 属于「已死凭证」，一律废弃并从登录页重新
      // 开始（自动填表免输入）。彻底消灭「过期凭证免密直达 → 登录 POST 带旧身份」的丑态。
      const stale = tokens.get(accountId);
      const staleCleared = Boolean(stale && (stale.token || stale.cookies?.length || stale.authUser));
      if (stale && staleCleared) {
        delete stale.token;
        delete stale.cookies;
        delete stale.authUser;
        tokens.set(accountId, stale);
        await persistTokens();
        void diag(`open(${accountId}) 无活页签：废弃残留登录态（v3.12.0 生命周期），走登录页自动填表`);
      }
      // 唯一例外（复制语义）：已有活页签但用户强制新开（forceNewTab）→ 视为「同账号
      // 复制页签」，token 活性由活页签背书，直达根路径保持身份稳定。
      const hasLiveSibling = boundTabsOf(accountId).length > 0;
      const scheme = account.scheme ?? 'https';
      const url = `${scheme}://${account.siteHost}${hasLiveSibling ? '/' : '/login'}`;
      const tab = await chrome.tabs.create({ url });
      tabId = tab.id!;
      void diag(`open(${accountId}) 新建 tab=${tabId} url=${url}`);
      void forensics('open-tab', {
        accountId,
        tabId,
        url,
        hasLiveSibling,
        staleCleared,
        cookieSnapshot: stale?.cookies?.length ?? 0,
        hadToken: Boolean(stale?.token),
      });
    } else {
      await chrome.tabs.update(tabId, { active: true });
      void diag(`open(${accountId}) 复用 tab=${tabId}`);
    }

    bindings.set(tabId, { accountId: account.id, host: account.siteHost });
    await persistBindings();
    void diag(`open(${accountId}) 绑定已持久化`);

    if (account.credentials) {
      try {
        const creds = await credentials.decryptCredentials(account.credentials);
        await setPendingAutoLogin(tabId, creds.username, creds.password);
        void diag(`open(${accountId}) 凭证解密并下发待登录`);
      } catch (e) {
        // 凭证损坏不应阻断网络平面安装（原实现会让 open() 在此中断）
        void diag(`open(${accountId}) 凭证解密失败：${e instanceof Error ? e.message : String(e)}`);
      }
    } else {
      void diag(`open(${accountId}) 无凭证字段`);
    }

    // 推送绑定种子：桥转发给 MAIN 壳（壳在 committed 前后到达均可自举）
    await pushBind(tabId);
    void diag(`open(${accountId}) pushBind 完成`);
    await applyTitle(tabId, account.tabName);
    await syncAccountRules(account.id, account.siteHost);
    void diag(`open(${accountId}) 完成 tabId=${tabId}`);
    return { tabId, reused: false };
  },

  /** 解绑单个标签页（不动账号数据）；最后一个绑定页签关闭 = 该账号登录态终结（v3.12.0） */
  async unbindTab(tabId: number): Promise<void> {
    const binding = bindings.get(tabId);
    if (bindings.delete(tabId)) {
      await persistBindings();
    }
    await tabRules.clearTab(tabId);
    try {
      await pushDown(tabId, { op: 'unbound' });
    } catch {
      // 页面可能已关闭
    }
    if (binding && boundTabsOf(binding.accountId).length === 0) {
      // 登录态生命周期跟随页签：无活页签 = 凭证视为已死，终结快照并清扫 jar 残留
      await terminateLoginState(binding.accountId, binding.host);
    }
  },

  /**
   * 站点自开的新页签亲子继承（window.open / target=_blank）：opener 已绑定账号 A
   * → 新页签登记为**收编候选**（v3.13 加固：此时 URL 尚未落地，不立即绑定）。
   * 首次导航 URL 由 onNavigation 确认：授权域内 → 正式收编（种子/规则此时才下发）；
   * 授权域之外或登录页 → 丢弃候选，页签保持原生（根本原则：外部链接零接触）。
   * 手动 Ctrl+T（无 openerTabId）连候选都不是。
   */
  async adoptFromOpener(tab: chrome.tabs.Tab): Promise<void> {
    const openerId = tab.openerTabId;
    const tabId = tab.id;
    if (openerId === undefined || tabId === undefined) {
      return;
    }
    const parent = bindings.get(openerId);
    if (!parent || bindings.has(tabId)) {
      return;
    }
    const account = await parallelStore.get(parent.accountId).catch(() => undefined);
    if (!account) {
      return;
    }
    pendingAdoptions.set(tabId, {
      accountId: account.id,
      host: parent.host,
      expires: Date.now() + PENDING_ADOPT_TTL,
    });
    void forensics('adopt-candidate', { tabId, accountId: account.id, host: parent.host });
    void diag(`adopt-candidate tab=${tabId} ← opener=${openerId} 账号=${account.id}（候选，待 URL 确认）`);
  },

  /** 删除账号：关闭其全部绑定标签页、摘除规则、清 token */
  async deleteAccount(accountId: string): Promise<void> {
    const tabs = boundTabsOf(accountId);
    for (const tabId of tabs) {
      await this.unbindTab(tabId);
    }
    if (tabs.length) {
      await chrome.tabs.remove(tabs).catch(() => undefined);
    }
    tokens.delete(accountId);
    await persistTokens();
    await parallelStore.delete(accountId);
  },

  /** ISOLATED 桥上行消息入口 */
  async handleBridge(payload: BridgeUpPayload, tabId: number | undefined): Promise<BridgeDownPayload | undefined> {
    if (payload.op === 'hello') {
      if (tabId !== undefined) {
        const binding = bindings.get(tabId);
        if (binding) {
          return buildBindPayload(binding.accountId, tabId);
        }
        if (pendingAdoptions.has(tabId)) {
          // v3.13 收编候选：URL 未确认前不灌种子/不置 settled——壳保持等待（hold），
          // URL 确认授权后再正式收编（防止候选期的种子流入外部域）
          return { op: 'hold' };
        }
      }
      return { op: 'unbound' };
    }
    if (tabId === undefined) {
      return undefined;
    }
    const binding = bindings.get(tabId);
    if (!binding) {
      return undefined;
    }
    if (payload.op === 'storageWrite') {
      const snap = tokens.get(binding.accountId) ?? {};
      if (payload.key === '__auth_token__') {
        if (payload.value === null) {
          // 页面内登出：清 token + 摘规则
          delete snap.token;
          delete snap.cookies;
          tokens.set(binding.accountId, snap);
          await persistTokens();
          await syncAccountRules(binding.accountId, binding.host);
          void forensics('logout', { accountId: binding.accountId, tabId });
        } else if (
          snap.token &&
          payload.value !== snap.token &&
          (() => {
            const a = jwtStableIdentity(snap.token!);
            const b = jwtStableIdentity(payload.value!);
            return a !== null && b !== null && a !== b;
          })()
        ) {
          // 身份叛逃：绑定页签被用户用于登录另一个账号（稳定载荷不同）
          await defectTabToRaw(tabId!, binding.accountId, binding.host, 'token 主体变更');
          return undefined;
        } else {
          await captureToken(binding.accountId, binding.host, payload.value);
          // 快照触发已内聚到 captureToken（首捕可能来自 authHeader 嗅探通道）
        }
      } else if (payload.key === '__auth_user__') {
        if (
          payload.value !== null &&
          snap.authUser &&
          (() => {
            const a = authUserIdentity(snap.authUser!);
            const b = authUserIdentity(payload.value!);
            // v3.12.0：两侧都成功提取才可比对——任一侧提取失败（非标准字段/原文）
            // 一律放行（原文比对会因时间戳差异把同账号误判为叛逃）
            return a !== null && b !== null && a !== b;
          })()
        ) {
          // 身份叛逃（用户信息主体变更；user 写入可能先于 token 写入到达）
          await defectTabToRaw(tabId!, binding.accountId, binding.host, '用户主体变更');
          return undefined;
        }
        snap.authUser = payload.value ?? undefined;
        tokens.set(binding.accountId, snap);
        await persistTokens();
      } else if (payload.key === '__device_fp__') {
        snap.deviceFp = payload.value ?? undefined;
        tokens.set(binding.accountId, snap);
        await persistTokens();
      }
      return undefined;
    }
    if (payload.op === 'authHeader') {
      // 二级捕获通道：fetch/XHR 出站 Authorization 头嗅探
      await captureToken(binding.accountId, binding.host, payload.value);
      return undefined;
    }
    if (payload.op === 'bagChanged') {
      // 袋→快照回流（v3.10.6）：页内 document.cookie 写入的实时并入
      try {
        await mergeBagIntoSnapshot(binding.accountId, binding.host, payload.bag ?? {});
      } catch (e) {
        void diag(`bagChanged(tab=${tabId}) 异常：${e instanceof Error ? e.message : String(e)}`);
      }
      return undefined;
    }
    if (payload.op === 'pageNames') {
      // v3.11 名称嗅探上行：交页面监视器建 guid→名称 表（host 由监视器自查）
      void pageMonitor.ingestNames(tabId, payload.names ?? [], payload.src ?? '');
      return undefined;
    }
    return undefined;
  },

  /**
   * UI 列表：返回实时状态（绑定标签页 / token / 网络平面健康）。
   * host 取绑定标签页的，无绑定时取账号自身 siteHost；授权健康读取 isEnforceable
   * 的同步缓存（par.list 已预先预热）——修复旧实现「无绑定账号永远显示离线、
   * 未授权状态不可见」的问题。
   */
  statusOf(account: ParallelAccount): { tabIds: number[]; hasToken: boolean; enforcementOff: boolean } {
    const tabs = boundTabsOf(account.id);
    const host = tabs.length ? bindings.get(tabs[0])?.host : account.siteHost;
    // 仅「明确缓存为 false」才判未授权；未知（缓存空/SW 冷启未暖）不得误报——
    // v3.10.1 修复：导入后即使权限已授，缓存未暖也会显示「未授权·已暂停」
    const enforcementOff = host ? enforcement.get(host) === false : true;
    return { tabIds: tabs, hasToken: Boolean(tokens.get(account.id)?.token), enforcementOff };
  },

  /** 账号改名后刷新所有绑定标签页标题 */
  async refreshTitle(accountId: string): Promise<void> {
    const account = await parallelStore.get(accountId);
    await Promise.all(boundTabsOf(accountId).map((t) => applyTitle(t, account.tabName)));
  },

  /** 诊断用内部状态快照（经 ql.diag 消息暴露给台架） */
  debugState(): Record<string, unknown> {
    return {
      bindings: Array.from(bindings.entries()).map(([t, b]) => ({ t, accountId: b.accountId, host: b.host })),
      tokenAccounts: Array.from(tokens.keys()),
      tokens: Array.from(tokens.entries()).map(([id, t]) => ({ id, hasToken: Boolean(t.token), authUser: t.authUser ?? null })),
      enforcement: Array.from(enforcement.entries()).map(([h, ok]) => `${h}=${ok}`),
    };
  },

  /* ------- 导航事件钩子（由 registerParallelHandlers 驱动） ------- */

  /** 标签页导航开始：重新推绑定种子与标题（SPA/整页刷新都会重置） */
  async onNavigation(tabId: number): Promise<void> {
    // v3.13 收编加固：候选页签的首个真实导航落地 → 按目标 URL 决定收编或放弃。
    // 授权域内（host 或 *.父域）→ 正式收编（此刻才发种子/装规则）；
    // 授权域之外 / 登录页 → 丢弃候选（页签保持原生，种子/规则零接触）。
    const pending = pendingAdoptions.get(tabId);
    if (pending) {
      if (Date.now() > pending.expires) {
        // 候选超时（30s 内未发生真实导航）：放弃并让壳回直通
        pendingAdoptions.delete(tabId);
        void pushDown(tabId, { op: 'unbound' });
        return;
      }
      let url = '';
      try {
        url = (await chrome.tabs.get(tabId)).url ?? '';
      } catch {
        pendingAdoptions.delete(tabId);
        return;
      }
      if (!/^https?:\/\//i.test(url)) {
        return; // 尚未发生真实导航（about:blank 等），继续等待
      }
      pendingAdoptions.delete(tabId);
      let urlHost = '';
      let path = '';
      try {
        const u = new URL(url);
        urlHost = u.hostname;
        path = u.pathname.toLowerCase();
      } catch {
        void diag(`adopt-candidate tab=${tabId} URL 不可解析：丢弃候选`);
        void pushDown(tabId, { op: 'unbound' });
        return;
      }
      const pendingHost = hostNoPortOf(pending.host);
      const parent = parentDomainOf(pendingHost);
      const sameSite = urlHost === pendingHost || urlHost.endsWith(`.${parent}`);
      if (!sameSite) {
        void diag(`adopt-candidate tab=${tabId} 目标 ${urlHost} 非授权域：丢弃候选（根本原则）`);
        void forensics('adopt-dropped', { tabId, accountId: pending.accountId, host: urlHost, reason: 'external-domain' });
        void pushDown(tabId, { op: 'unbound' });
        return;
      }
      if (path.includes('login')) {
        // v3.10.4 语义：继承页签进登录页 = 用户当独立浏览器用 → 保持原生（丢弃候选）
        void diag(`adopt-candidate tab=${tabId} 进入登录页：丢弃候选（转原始）`);
        void pushDown(tabId, { op: 'unbound' });
        return;
      }
      const account = await parallelStore.get(pending.accountId).catch(() => undefined);
      if (!account) {
        void pushDown(tabId, { op: 'unbound' });
        return;
      }
      if (!tokens.get(account.id)?.token) {
        await capturePreJar(account.id, pending.host);
      }
      bindings.set(tabId, { accountId: account.id, host: pending.host, adopted: true });
      await persistBindings();
      await syncAccountRules(account.id, pending.host);
      await pushBind(tabId);
      await applyTitle(tabId, account.tabName);
      void forensics('adopt', { tabId, accountId: account.id, host: pending.host, url });
      void diag(`adopt tab=${tabId} ← 账号=${account.id}（URL 确认后正式收编）`);
      return;
    }

    const binding = bindings.get(tabId);
    if (!binding) {
      return;
    }
    // 授权域护栏：绑定页签漫游到其它未授权 http(s) 域 → 解绑摘规则，
    // 防止账号种子/身份头流向非授权域（异域弹窗跳转、手动改址）。自身 host 不受影响。
    let url = '';
    try {
      url = (await chrome.tabs.get(tabId)).url ?? '';
    } catch {
      return;
    }
    if (/^https?:\/\//i.test(url)) {
      const host = new URL(url).hostname;
      if (host !== binding.host && !(await isEnforceable(host))) {
        void diag(`onNavigation 非授权域 ${host}：解除 tab=${tabId} 绑定`);
        await this.unbindTab(tabId);
        return;
      }
      // 继承页签进入登录页（v3.10.4）：用户把它当独立浏览器用了——在输入账密之前
      // 转回原始页签（解绑+重载），B 的登录从原生状态开始，与账号 A 互不干扰。
      // 仅限亲子继承页签；账号自己的页签会话过期重登不受影响。
      if (binding.adopted) {
        let path = '';
        try {
          path = new URL(url).pathname.toLowerCase();
        } catch {
          path = '';
        }
        if (path.includes('login')) {
          void diag(`onNavigation 继承页签 tab=${tabId} 进入登录页 ${path}：转原始页签`);
          await this.unbindTab(tabId);
          void chrome.tabs.reload(tabId).catch(() => undefined);
          return;
        }
      }
    }
    const account = await parallelStore.get(binding.accountId).catch(() => undefined);
    if (!account) {
      return;
    }
    await pushBind(tabId);
    await applyTitle(tabId, account.tabName);
  },

  /** SW 冷启动恢复：绑定表 + token/Cookie 快照 → 重建内存态与规则（授权健康门控） */
  async restore(): Promise<void> {
    void diag('parallelSession.restore 开始');
    await readState();
    enforcement.clear();
    const persisted = new Map<number, { host: string; token: string | null; cookie: string | null }>();
    for (const [tabId, b] of bindings) {
      if (await isEnforceable(b.host)) {
        persisted.set(tabId, {
          host: b.host,
          token: tokens.get(b.accountId)?.token ?? null,
          cookie: cookieHeaderOf(b.accountId),
        });
      }
    }
    await tabRules.restore(persisted);
    void diag(`parallelSession.restore 完成 persisted=${persisted.size}`);
  },

  async handleTabRemoved(tabId: number): Promise<void> {
    pendingAdoptions.delete(tabId); // 候选页签关闭：清理（未正式收编无残留）
    if (bindings.has(tabId)) {
      await this.unbindTab(tabId);
    }
  },
};

async function tabStillAlive(tabId: number): Promise<boolean> {
  try {
    await chrome.tabs.get(tabId);
    return true;
  } catch {
    return false;
  }
}

/** 构造带种子快照的绑定载荷（hello 应答与主动推送共用）。
 *  authUser 恒输出：有值写值、无值输出 ''（种子空值 = 壳显式清除命名空间残留键） */
function buildBindPayload(accountId: string, tabId?: number): BridgeDownPayload {
  const snap = tokens.get(accountId);
  const seed: Record<string, string> = {};
  if (snap?.token) {
    seed['__auth_token__'] = snap.token;
  } else {
    // v3.12.3：无快照 token = 登录前窗口——**显式清空命名空间残留的上一会话 token**。
    // 否则平台登录页读到残留 token 自动续用（authHeader 嗅探捕为「首捕」），用户
    // 再次登录签发的新 token（AuthCode 已变）会被身份护栏误判为异账号而拦截，
    // 快照卡死旧 token → API 全 401 → 反复登录失败（用户实测链路实锤）。
    seed['__auth_token__'] = '';
  }
  seed['__auth_user__'] = snap?.authUser ?? '';
  if (snap?.deviceFp) {
    seed['__device_fp__'] = snap.deviceFp;
  }
  // 账号 Cookie 快照的权威视图（非身份键；v3.12.1）：绑定时壳把袋整体同步到该视图。
  // 无 token（登录前窗口）时为空对象 = 清空上一会话残留的陈旧袋值——
  // 否则陈旧 Cookie 会经页内读取/袋回流毒化登录 POST（快捷登录登出后失败的根因）。
  const bag: Record<string, string> = {};
  for (const c of snap?.cookies ?? []) {
    if (!IDENTITY_COOKIE_BLACKLIST.has(c.name)) {
      bag[c.name] = c.value;
    }
  }
  return { op: 'bind', accountId, tabId, seed, bag };
}

async function pushBind(tabId: number): Promise<void> {
  const binding = bindings.get(tabId);
  if (!binding) {
    return;
  }
  // 携带账号快照作种子：壳激活瞬间即有正确 token/身份，杜绝跨账号读取窗口
  await pushDown(tabId, buildBindPayload(binding.accountId, tabId));
}

async function pushDown(tabId: number, payload: BridgeDownPayload): Promise<void> {
  try {
    await chrome.tabs.sendMessage(tabId, { type: CONTENT_MESSAGE.bridgeDown, payload });
  } catch {
    // 内容脚本未就绪或页面已关闭：onNavigation/loading 事件会再次推送
  }
}

/** 装载事件监听；由 service-worker 调用一次 */
export function registerParallelHandlers(): void {
  void parallelSession.restore();

  chrome.runtime.onStartup?.addListener(() => {
    void parallelSession.restore();
  });

  chrome.tabs.onRemoved.addListener((tabId) => {
    void parallelSession.handleTabRemoved(tabId);
  });

  // 站点自开的新页签：opener 已绑定 → 亲子继承（低代码平台弹窗编辑保持同一账号身份）
  chrome.tabs.onCreated.addListener((tab) => {
    void parallelSession.adoptFromOpener(tab);
  });

  // 导航提交近似信号：status=loading 时重推种子/标题（不引入 webNavigation 权限）
  chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
    if (changeInfo.status === 'loading') {
      void parallelSession.onNavigation(tabId);
    }
  });

  // 会话卫生：持续驱逐重入真实 jar 的扩展账号 Cookie（v3.10.2）
  chrome.cookies.onChanged.addListener((change) => {
    void evictJarCookie(change);
  });

  // 快照动态化（v3.10.6）+ 写入者归属（v3.11.1）：绑定页签收到的响应 Set-Cookie
  // 实时并入账号快照，并记录写入者归属（根本原则：原生页签写入不受卫生机制影响）。
  // 观察型 webRequest（MV3 允许；无 host 权限的站点不产生事件——授权门控天然成立）。
  if (chrome.webRequest?.onHeadersReceived) {
    chrome.webRequest.onHeadersReceived.addListener(
      (details: {
        tabId: number;
        url: string;
        statusCode?: number;
        type?: string;
        responseHeaders?: { name: string; value?: string }[];
      }) => {
        trackCookieAttribution(details);
        void captureResponseCookies(details);
        void reportFailureStatus(details);
      },
      { urls: ['*://*/*'] },
      // extraHeaders：Set-Cookie 头需显式请求可见性（Chrome 72+）
      ['responseHeaders', 'extraHeaders'],
    );
  }

  void cleanupStaleBindings();
}

/* ---------------- 打开失败自学习（v3.10.9） ----------------
 * 探测歧义的兜底：内网 https 自签证书会被 SW fetch 误判为「无 https」，或存量账号
 * 默认 https 而站点实为纯 http——打开落 chrome-error 页时，scheme 类网络错误触发
 * 协议翻转写回账号档案并原页签重开（用户无感；15s 防抖避免循环翻转）。 */

const SCHEME_FLIP_ERRORS = new Set([
  'net::ERR_SSL_PROTOCOL_ERROR',
  'net::ERR_CONNECTION_REFUSED',
  'net::ERR_CONNECTION_RESET',
  'net::ERR_EMPTY_RESPONSE',
]);
const schemeFlipAt = new Map<string, number>();

/** scheme 类网络错误判定（ERR_SSL_PROTOCOL_ERROR = 443 无 TLS；REFUSED/RESET/EMPTY_RESPONSE = 端口未服务） */
export function isSchemeFlipError(error: string): boolean {
  return SCHEME_FLIP_ERRORS.has(error);
}

export async function handleOpenError(tabId: number, error: string): Promise<boolean> {
  const binding = bindings.get(tabId);
  if (!binding || !SCHEME_FLIP_ERRORS.has(error)) {
    return false;
  }
  const account = await parallelStore.get(binding.accountId).catch(() => null);
  if (!account) {
    return false;
  }
  const now = Date.now();
  if (now - (schemeFlipAt.get(binding.accountId) ?? 0) < 15_000) {
    return false; // 翻转冷却中：让当前重开结果先落地
  }
  const current = account.scheme ?? 'https';
  const flipped: Scheme = current === 'https' ? 'http' : 'https';
  schemeFlipAt.set(binding.accountId, now);
  await parallelStore.updateScheme(binding.accountId, flipped);
  const hasToken = Boolean(tokens.get(binding.accountId)?.token);
  const url = `${flipped}://${account.siteHost}${hasToken ? '/' : '/login'}`;
  void diag(`handleOpenError(${binding.accountId}) ${error} → scheme 自学习 ${current}→${flipped}，重开 ${url}`);
  await chrome.tabs.update(tabId, { url });
  return true;
}

/** 恢复时清理指向已不存在标签页的陈旧绑定 */
async function cleanupStaleBindings(): Promise<void> {
  for (const tabId of Array.from(bindings.keys())) {
    const alive = await tabStillAlive(tabId);
    if (!alive) {
      await parallelSession.handleTabRemoved(tabId);
    }
  }
}
