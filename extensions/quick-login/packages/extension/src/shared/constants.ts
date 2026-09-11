/**
 * 扩展版本号。**唯一真源 = manifest.json**（`tools/bump.py` 五写机制会同步它），
 * 因此 UI 展示的版本号自动跟随项目升版，无需任何手工同步。
 *
 * 历史教训：此处曾硬编码上游版本常量（`EXT_VERSION = '3.13.2'`），而 bump.py 不写本文件
 * → 弹窗、扩展图标徽标、管理页三处版本号在上游同步后永远停在旧号（实测：项目已是 0.2.21，
 * 弹窗仍显示 v3.13.2）。**不要再引入手写版本常量。**
 *
 * 用函数而非模块级常量：内容脚本也会 import 本模块（其它符号），模块级求值会在
 * 不保证 `chrome.runtime` 的场景下抛错；此处 try/catch 兜底为 '0.0.0'。
 */
export function extVersion(): string {
  try {
    return chrome.runtime.getManifest().version;
  } catch {
    return '0.0.0';
  }
}
export const IDB_NAME = 'sessionbox-reborn';
export const IDB_VERSION = 2;
export const IDB_STORE_SESSIONS = 'sessions';
export const IDB_STORE_ACCOUNTS = 'accounts';

/** 存在 session 级 chrome.storage.session 中的键 */
export const SESSION_KEYS = {
  /** 待自动登录凭证前缀（实际键为 `sb:pendingAutoLogins:<tabId>`，见 core/pending-login.ts） */
  pendingAutoLogins: 'sb:pendingAutoLogins',
  /** 并行账号的 tabId ↔ accountId 绑定表 */
  parTabBindings: 'ql:parTabBindings',
  /** 并行账号捕获到的运行时凭证快照（Bearer token 等，随浏览器会话存活） */
  parTokens: 'ql:parTokens',
} as const;

/** 存在 chrome.storage.local 中的站点清单键 */
export const LOCAL_KEYS = {
  siteGrants: 'sb:siteGrants',
  /** 手动停用的站点（Chrome 拒绝回收授权时本地封锁，不再对其安装改头规则） */
  blockedHosts: 'ql:blockedHosts',
  /** 记忆的盒子清单（空盒子也保留；缺省「默认盒子」不入库） */
  boxList: 'ql:boxes',
  /** 默认盒子的自定义名称（未归盒账号的归宿；缺省「默认盒子」） */
  defaultBox: 'ql:defaultBox',
  /** 被禁用的盒子名单（轮盘跳过切换；空默认盒自动禁用） */
  disabledBoxes: 'ql:disabledBoxes',
  /** 站点协议 hint（v3.10.9：授权时从用户输入 URL 解析；账号创建时优先采用） */
  siteSchemes: 'ql:siteSchemes',
  /** 登录失败现场取证环形缓冲（v3.12.2：生命周期 + 自动填表逐事件） */
  forensics: 'ql:forensics',
} as const;

/** background 向内容脚本下发的消息 type */
export const CONTENT_MESSAGE = {
  setTitle: 'sb:setTitle',
  autoLogin: 'sb:autoLogin',
  autoLoginRequest: 'sb:autoLoginRequest',
  /** 自动填表事件上报（v3.12.2 取证：填充/点击/让位/被拒逐事件入 forensics） */
  autoLoginEvent: 'sb:autoLoginEvent',
  /** ISOLATED 桥 → background（双向通路的上行） */
  bridgeUp: 'ql:bridgeUp',
  /** background → ISOLATED 桥（下行） */
  bridgeDown: 'ql:bridgeDown',
} as const;

/** window.postMessage 的 source 标识（桥 ↔ MAIN 壳内部通路） */
export const WINDOW_CHANNEL = {
  pageToBridge: 'QL_PAGE_TO_BRIDGE',
  bridgeToPage: 'QL_BRIDGE_TO_PAGE',
} as const;

/**
 * 以下「壳协议键」是 MAIN world 壳（content/shield-main.ts）与 background/ISOLATED 桥之间的
 * 约定，两端必须逐字一致 → **只在本模块定义一次**，其余文件一律 import，禁止再写字面量。
 * （2026-09-11 收敛：此前 `__ql_ns_`/`__ql_cookies__`/`__auth_token__`/桥消息源标识
 *  在 shield-main.ts 与 parallel-session.ts 各自硬编码，共 6 处重复，改一处即静默失配。）
 *
 * ⚠ 本模块会被 **MAIN world** 的内容脚本 import：模块顶层只允许放纯数据/纯函数，
 * 不得出现任何依赖浏览器扩展 API 的顶层求值（例如 `chrome.runtime`），否则会在宿主页
 * 抛错并破坏隔离壳。参考 `extVersion()`——正是因为这一点才写成函数而非模块级常量。
 */

/** 站点 JWT 持久化副本的键（袋内同名；token 捕获与显式清除都用它） */
export const SHIELD_TOKEN_KEY = '__auth_token__';
/** 身份展示键（用户名）：壳上报写入，后台据此判定「用户主体变更」 */
export const SHIELD_USER_KEY = '__auth_user__';
/** 设备指纹展示键：随种子回灌，不参与身份判定 */
export const SHIELD_DEVICE_FP_KEY = '__device_fp__';

/**
 * 需要按账号隔离、并在被写入时上报 background 的共享 localStorage 键。
 * 与目标站约定：`__auth_token__` 为 JWT 持久化副本；后两项为身份与设备指纹展示键。
 */
export const SHIELD_WATCH_KEYS: readonly string[] = [
  SHIELD_TOKEN_KEY,
  SHIELD_USER_KEY,
  SHIELD_DEVICE_FP_KEY,
];

/** 账号命名空间内保存「虚拟 Cookie 袋」（JSON 序列化的 document.cookie 视图）的键 */
export const SHIELD_COOKIE_BAG_KEY = '__ql_cookies__';

/**
 * 账号命名空间前缀。绑定标签页内，localStorage 全部键读写都会重定向到
 * `__ql_ns_<accountId>__<原键>`，实现同 origin 下多账号物理隔离。
 */
export function shieldNsPrefix(accountId: string): string {
  return `__ql_ns_${accountId}__`;
}

/** 新会话默认配色（蓝白主题内的强调色轮换） */
export const SESSION_COLORS = [
  '#1E6FFF',
  '#0FA3B1',
  '#7C5CFF',
  '#FF7A1A',
  '#22C55E',
] as const;
