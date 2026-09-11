export interface Session {
  id: string;
  /** 会话展示名 */
  name: string;
  /** 账号名/用户名 —— 用作标签页标题 */
  accountAlias: string;
  color: string;
  /** 绑定的站点 host，例如 example.com */
  siteHost: string;
  /** 站点协议（v3.10.9）：缺省 = https（兼容存量）；打开 URL 与 Cookie 查询跟随 */
  scheme?: 'http' | 'https';
  /** 加密存储的账号密码（可选） */
  credentials?: EncryptedCredentials;
  createdAt: number;
  updatedAt: number;
}

/** 加密后的账号密码 */
export interface EncryptedCredentials {
  /** 加密后的用户名（Base64） */
  encryptedUsername: string;
  /** 加密后的密码（Base64） */
  encryptedPassword: string;
  /** 用户名加密使用的 IV（Base64） */
  iv: string;
  /** 密码加密使用的 IV（Base64） */
  ivPassword: string;
  /** 加密时间戳 */
  encryptedAt: number;
}

export interface SiteGrant {
  host: string;
  grantedAt: number;
}

/**
 * 浏览器并行账号（纯扩展模式，不依赖本地引擎）。
 * 每个账号可打开多个标签页并行在线；页签名用于标签标题展示，可自定义。
 */
export interface ParallelAccount {
  id: string;
  /** 绑定的站点 host */
  siteHost: string;
  /** 站点协议（v3.10.9）：缺省 = https（兼容存量）；打开 URL 与 Cookie 查询跟随。
   *  来源：添加时解析用户输入/自动探测（https 优先），打开失败时自学习翻转。 */
  scheme?: 'http' | 'https';
  /** 自定义页签名 —— 该账号标签页的标题 */
  tabName: string;
  /** 账号名（登录用户名） */
  username: string;
  /** 加密存储的密码等凭证 */
  credentials?: EncryptedCredentials;
  color: string;
  /** 所属盒子（收纳分组）；缺省 = 「默认盒子」 */
  box?: string;
  createdAt: number;
  updatedAt: number;
}

/** 并行账号运行时状态（由 background 依据绑定表与 token 快照实时计算） */
export interface ParallelAccountStatus {
  tabIds: number[];
  hasToken: boolean;
  /** 站点授权缺失/被停用：DNR 改头与 Cookie 剥离不生效，功能暂停 */
  enforcementOff?: boolean;
}

/** ISOLATED 桥 → background 的上行载荷 */
export type BridgeUpPayload =
  | { op: 'hello'; url: string }
  | { op: 'storageWrite'; key: string; value: string | null }
  | { op: 'authHeader'; value: string }
  | { op: 'journalRollbackDone' }
  /** 页内 document.cookie 写入的 Cookie 袋全量视图（v3.10.6 袋→快照回流）：
   *  绑定页签的 Cookie 写入被 MAIN 壳虚拟化进袋子，永不落真实 jar——服务端登录后
   *  由页内 JS 写入的票据/凭据若不回流快照，网络平面回放永远缺失 */
  | { op: 'bagChanged'; bag: Record<string, string> };

/** background → ISOLATED 桥的下行载荷 */
export type BridgeDownPayload =
  /** 绑定账号并附带初始快照种子（token 等，用于壳激活瞬间同步灌入命名空间）；
   *  tabId 供壳做页面层缓存分区（_qlck=t<tabId>，DNR urlTransform Chrome 不支持）；
   *  bag = 账号 Cookie 快照的权威视图（非身份键）——绑定时袋整体同步到该视图
   *  （v3.12.1：修复登出/过期后重开时命名空间陈旧袋复活并毒化登录 POST） */
  | { op: 'bind'; accountId: string; tabId?: number; seed?: Record<string, string>; bag?: Record<string, string> }
  | { op: 'unbound' }
  /** v3.13 收编加固：亲子继承候选页签（URL 未确认授权前）收到 hello 的应答——
   *  壳保持等待（不置 settled/unbound），待 URL 确认授权后正式收编并灌种子 */
  | { op: 'hold' }
  /** 身份叛逃处置：回滚本页会话对命名空间的全部写入（含 Cookie 袋），恢复到页签打开前状态 */
  | { op: 'journalRollback' }
  /** 清扫本账号命名空间的 IDB / CacheStorage 共享缓存（叛逃页签写入的他人数据） */
  | { op: 'nsWipeShared' };
