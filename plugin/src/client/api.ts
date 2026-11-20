/**
 * 浏览器侧唯一的数据入口。
 *
 * ★ 走**同源相对路径**（`api/akso-workbench/*`，无前导斜杠）：GUI 用 `<base href="./">`
 *   提供，根绝对路径 `/api/...` 会在子路径部署下逃出前缀（与 skill-explorer 同款处理）。
 * ★ 组件里不直接 `fetch`：都在这里收口，便于统一错误语义。
 * ★ **所有写操作都返回整份新快照**（host 侧写完后统一回拉 `/api/ui/state`）——
 *   因此这里不需要"本地猜一个结果"，数据一致性由 host 的快照保证。
 */
import type { AnalysisWorkflow } from '../analysis-workflow.ts'
import type { AnalysisField } from '../analysis-fields.ts'

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
  review?: {
    path: string
    kind: 'create' | 'update'
    sessionId?: string
    title: string
    problem: string
    approach: string
    expected: string[]
  }
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
  try {
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
    // 写操作成功也留一条：'用户点了什么'的粒度就在 API 这一层
    if (init?.method === 'POST' && path !== 'journal/log') track('client.api', { path })
    return payload as T
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    track('client.api.error', { path, message })
    throw error
  }
}

/**
 * 浏览器事件上报（运行日志）：**不等待、不抛错** —— 日志写不进去也不能影响用户操作。
 * 只收 `client.*` / `ui.*` 前缀（host 侧同样校验），避免变成任意写口。
 */
export type JournalKind = `client.${string}` | `ui.${string}`

export function track(kind: JournalKind, data: Record<string, unknown> = {}): void {
  try {
    void fetch(url('journal/log'), {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', [CLIENT_HEADER]: '1' },
      body: JSON.stringify({ kind, data }),
    }).catch(() => {})
  } catch {
    // 网络不可用/页面正在卸载：忽略
  }
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
  workflowIds: string[]
  workflowName: string | null
  targetStatusId: string | null
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

export interface AnalysisHistoryView {
  id: string
  createdAt: string
  query: string
  /** 成功 / 失败都进历史；失败条目带 error，没有结果明细。 */
  status: 'ok' | 'failed'
  error?: string
  objectCode: string
  objectName: string
  statuses: number
  enabledStatuses: number
  userActions: number
  enterActions: number
  fields: number
  workflows: number
  baseUrl: string
  account: string
}

/** 打开一条历史：成功给 result，失败给 error。 */
export interface AnalysisHistoryDetailView {
  status: 'ok' | 'failed'
  query: string
  createdAt: string
  error?: string
  result?: AnalysisResultView
}

export interface AnalysisResultView {
  source: { baseUrl: string; account: string }
  object: { id: string; code: string; name: string; lifecycleId: string; enableLifeCycle: boolean }
  lifecycle: { id: string; name: string } | null
  statuses: AnalysisStatusView[]
  fields: AnalysisField[]
  workflows: AnalysisWorkflow[]
  totals: {
    statuses: number
    enabledStatuses: number
    userActions: number
    enterActions: number
    fields: number
  }
  enterCondition: { count: number; configured: boolean; readable: boolean }
  warnings: string[]
}
/* ── 运行日志：页面「运行日志」页的数据源 ── */

export interface JournalFileView {
  name: string
  day: string
  seq: number
  bytes: number
  events: number
}

export interface JournalSummaryView {
  dir: string
  retentionDays: number
  events: number
  bytes: number
  files: JournalFileView[]
  oldest: string | null
  newest: string | null
}

export interface JournalDownloadView {
  blob: Blob
  fileName: string
  events: number
  truncated: boolean
}

/**
 * 下载运行日志：走 `fetch` 而不是 `call()`（响应是文件，不是 JSON）。
 * 文件名从 `Content-Disposition` 取；服务端不给就退回一个带天数的默认名。
 */
export async function downloadJournal(days: number): Promise<JournalDownloadView> {
  const response = await fetch(url(`journal/download?days=${days}`), { cache: 'no-store' })
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>
    throw new Error(typeof payload.error === 'string' ? payload.error : `HTTP ${response.status}`)
  }
  const blob = await response.blob()
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const matched = /filename="([^"]+)"/.exec(disposition)
  return {
    blob,
    fileName: matched?.[1] ?? `akso-workbench-journal-${days}d.jsonl`,
    events: Number(response.headers.get('X-Journal-Events') ?? '0') || 0,
    truncated: response.headers.get('X-Journal-Truncated') === '1',
  }
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
  analysisHistory: () => call<{ items: AnalysisHistoryView[]; dir?: string }>('analysis/history'),
  analysisHistoryItem: (id: string) =>
    call<AnalysisHistoryDetailView>(`analysis/history?id=${encodeURIComponent(id)}`),

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
      /** 「系统常用映射表.md」：按作用域分组。页面暂不渲染，保留给排查与后续扩展。 */
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

  /** 运行日志概览：事件数、体积、时间范围与文件列表。 */
  journalSummary: () => call<{ ok: boolean; summary: JournalSummaryView }>('journal/summary'),
}
