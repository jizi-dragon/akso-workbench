/** 扩展版本号（与根 package.json / manifest.json 保持同步；UI 中显性展示以区分构建） */
export const EXT_VERSION = '3.13.2';
export const IDB_NAME = 'sessionbox-reborn';
export const IDB_VERSION = 2;
export const IDB_STORE_SESSIONS = 'sessions';
export const IDB_STORE_ACCOUNTS = 'accounts';

/** 存在 session 级 chrome.storage.session 中的键 */
export const SESSION_KEYS = {
  sessionTabBindings: 'sb:tabBindings',
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
  /** 最近配置页 MRU（v3.13：按 host 分组，每组至多 RECENT_PAGES_MAX 条；**仅记录绑定页签**） */
  recentPages: 'ql:recentPages',
  /** 登录失败现场取证环形缓冲（v3.12.2：生命周期 + 自动填表逐事件） */
  forensics: 'ql:forensics',
} as const;

/** 最近配置页每组容量（需求：最近 5 个） */
export const RECENT_PAGES_MAX = 5;

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
 * 需要按账号隔离、并在被写入时上报 background 的共享 localStorage 键。
 * 与目标站约定：__auth_token__ 为 JWT 持久化副本；后两项为身份与设备指纹展示键。
 */
export const SHIELD_WATCH_KEYS = ['__auth_token__', '__auth_user__', '__device_fp__'] as const;

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
