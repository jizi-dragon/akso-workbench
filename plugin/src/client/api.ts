/**
 * 浏览器侧唯一的数据入口。
 *
 * ★ 走**同源相对路径**（`api/akso-workbench/*`，无前导斜杠）：GUI 用 `<base href="./">`
 *   提供，根绝对路径 `/api/...` 会在子路径部署下逃出前缀（与 skill-explorer 同款处理）。
 * ★ 组件里不直接 `fetch`：都在这里收口，便于统一错误语义。
 * ★ **所有写操作都返回整份新快照**（host 侧写完后统一回拉 `/api/ui/state`）——
 *   因此这里不需要"本地猜一个结果"，数据一致性由 host 的快照保证。
 */

export interface CloudAccount {
  id: string
  siteId: string
  siteName: string
  siteHost: string
  baseUrl: string
  username: string
  tabName: string
  role: string
  tags: string[]
  note: string
  status: string
  box: string
  scheme: string
  hasPassword: boolean
  createdAt: number
  updatedAt: number
}

export interface CloudSite {
  id: string
  name: string
  baseUrl: string
  host: string
  note: string
  accountCount: number
}

export interface CloudBox {
  name: string
  isDefault: boolean
  disabled: boolean
  autoDisabled: boolean
  count: number
  siteId?: string
  siteName?: string
}

export interface ServerState {
  format: 'akso-workbench-state'
  loggedIn: boolean
  profile: { email: string; displayName: string; baseUrl: string } | null
  revision: number | null
  syncedAt: number | null
  stale: boolean
  syncError: string | null
  accounts: CloudAccount[]
  sites: CloudSite[]
  boxes: CloudBox[]
  defaultBox: string
  counts: { accounts: number; sites: number; boxes: number }
  currentAccountId: string | null
  /** 配置池：允许 agent 登录的账号（底部账号条只列这些） */
  poolAccountIds: string[]
  /** 工具刚想写入但没有可用账号：界面据此自动弹一次选账号面板 */
  needsAccount: boolean
  needsAccountSince: number | null
}

/** 审批窗里的一步：给人看的「要写什么」，不是给机器的 blueprint。 */
export interface ApprovalStepView {
  index: number
  action: 'create' | 'update'
  kind: string
  title: string
  method: string
  path: string
  risk: string
  irreversible: boolean
  params: unknown
  warnings: string[]
}

export interface ApprovalReportView {
  planId: string
  title: string
  env: string
  baseUrl: string
  host: string
  account: string
  accountId: string | null
  summary: { create: number; update: number; skip: number; irreversible: number }
  /** 业务摘要（弹窗顶部）：只说新建 / 更新了什么，不列接口与参数。 */
  business: Array<{ action: 'create' | 'update'; kind: string; names: string[] }>
  steps: ApprovalStepView[]
  createdAt: number
  expiresAt: number
  hash: string
}

export interface PendingApprovalView {
  id: string
  planId: string
  createdAt: number
  expiresAt: number
  report: ApprovalReportView
}

const BASE = 'api/akso-workbench'

/**
 * 写请求必须带这个头（host 侧 `requestFence` 的硬闸门）：
 * 跨站页面既设不了自定义头，也过不了 preflight —— 我们自己不发 CORS 头，所以别的网页
 * 没法借你的浏览器往本机 API 写东西。只读的 GET 不要求，便于 curl 探活。
 */
export const CLIENT_HEADER = 'x-akso-workbench'

function url(path: string): string {
  return new URL(`${BASE}/${path}`, document.baseURI).toString()
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url(path), {
    cache: 'no-store',
    ...init,
    headers: { 'Content-Type': 'application/json', [CLIENT_HEADER]: '1', ...(init?.headers ?? {}) },
  })
  const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>
  if (!response.ok) {
    const message = typeof payload.error === 'string' ? payload.error : `HTTP ${response.status}`
    throw new Error(message)
  }
  return payload as T
}

const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

/** eGMP 会话（host 侧持有 JWT；这里永远拿不到令牌，只有"登没登录、哪个账号、哪个环境"）。 */
export interface EgmpSessionView {
  loggedIn: boolean
  baseUrl: string | null
  account: string | null
  savedAt: number | null
}

/**
 * 令牌详情：**只有人主动点「查看」时才取**（`config/token`，GET 但要求自定义头）。
 * `issuedAt` / `expiresAt` 是 host 从 JWT 的 `iat` / `exp` 解出来的毫秒时间戳，没解到就是 null。
 */
export interface EgmpTokenView extends EgmpSessionView {
  token: string | null
  issuedAt: number | null
  expiresAt: number | null
}

export interface AnalysisActionView {
  id: string
  code: string
  name: string
  description: string
  kind: 'user' | 'enter'
  behaviorTypes: number[]
  behaviorLabels: string[]
  workflowId: string | null
  workflowName: string | null
  configCount: number
}

export interface AnalysisStatusView {
  id: string
  name: string
  code: string
  sort: number
  isEnabled: boolean
  userActions: AnalysisActionView[]
  enterActions: AnalysisActionView[]
}

export interface AnalysisResultView {
  object: { id: string; code: string; name: string; lifecycleId: string; enableLifeCycle: boolean }
  lifecycle: { id: string; name: string } | null
  statuses: AnalysisStatusView[]
  totals: { statuses: number; enabledStatuses: number; userActions: number; enterActions: number }
  enterCondition: { count: number; configured: boolean; readable: boolean }
  warnings: string[]
}
export const api = {
  state: () => call<ServerState>('state'),

  /** ★ 地址固定在 host 侧，这里不再传 baseUrl（服务地址不是用户可配项）。 */
  login: (input: { email: string; password: string }) => call<ServerState>('cloud/login', post(input)),
  logout: () => call<ServerState>('cloud/logout', post({})),
  sync: () => call<ServerState>('cloud/sync', post({})),

  /** 设备流：起一个授权请求，host 会顺手打开系统浏览器。 */
  deviceStart: () =>
    call<{ userCode: string; verificationUrl: string; expiresIn: number; interval: number }>(
      'cloud/device/start',
      post({}),
    ),
  /** 设备流：轮询；批准后 host 已经存好会话并回拉快照，这里直接拿到整份 state。 */
  devicePoll: () =>
    call<{ status: string; state?: ServerState }>('cloud/device/poll', post({})),

  createAccount: (input: {
    siteId: string
    username: string
    password: string
    tabName?: string
    note?: string
    box?: string
  }) => call<ServerState>('accounts/create', post(input)),
  setCurrent: (id: string) => call<ServerState>('accounts/current', post({ id })),

  /** 配置池：把账号加入/移出 agent 可选登录的范围。 */
  togglePool: (id: string, inPool: boolean) => call<ServerState>('pool/toggle', post({ id, inPool })),

  createSite: (input: { name?: string; baseUrl: string; note?: string }) =>
    call<ServerState>('sites/create', post(input)),

  renameBox: (source: string, target: string) => call<ServerState>('boxes/rename', post({ source, target })),
  bindBoxSite: (name: string, siteId: string) => call<ServerState>('boxes/bind-site', post({ name, siteId })),

  /* ── eGMP 会话：只走 akso-cloud 代登录，浏览器不保存任何凭据 ── */
  /** 令牌原文 + 到期时间（账号条里点「查看」才调）。 */
  configToken: () => call<EgmpTokenView>('config/token'),
  /** 页面开关：写入前是否弹审批窗（环境变量优先，见 capabilities 返回值）。 */
  configPrefs: (requireApproval: boolean) =>
    call<{ ok: boolean; saved: boolean; requireApproval: boolean; env: string | null }>('config/prefs', post({ requireApproval })),
  /** 对话里那张审批窗：当前挂起的一条（没有就 null）。 */
  configApprovalPending: () => call<{ pending: PendingApprovalView | null }>('config/approval/pending'),
  configApprovalDecide: (id: string, verdict: 'approve' | 'deny' | 'supplement', note?: string) =>
    call<{ ok: boolean; verdict: string }>('config/approval/decide', post({ id, verdict, note })),
  /** 能力目录（工具页的数据源）。返回的 items 形状由 tools.tsx 自己声明。 */
  /** 对象分析：输入对象 id/code，读生命周期、状态、动作。 */
  analyzeObject: (query: string) => call<AnalysisResultView>('analysis/object', post({ query })),

  configCapabilities: () =>
    call<{
      session: EgmpSessionView
      groups: Array<{ domain: string; label?: string; items: unknown[] }>
      requireApproval: boolean
      approvalEnv: string | null
      /** 「基础API使用指南.md」的读取结果：改完文档刷新页面即生效。 */
      guide?: {
        path: string | null
        updatedAt: string | null
        warnings: string[]
        coverage: { total: number; documented: number }
      }
      /** 「标准配置使用规范.md」：只有部分接口有标准方案。 */
      recipesGuide?: {
        path: string | null
        updatedAt: string | null
        warnings: string[]
        coverage: { total: number; documented: number; recipes: number }
      }
      /** 「系统常用映射表.md」：按作用域分组，界面按当前接口域过滤。 */
      actionMap?: {
        path: string | null
        updatedAt: string | null
        warnings: string[]
        groups: Array<{
          name: string
          scope: string[]
          rows: Array<{ value: string; label: string; note?: string }>
        }>
      }
    }>('config/capabilities'),
}
