/**
 * 浏览器侧单一状态源（所有座位共用）。
 *
 * 为什么不是每页各取一份：
 *   · 账号条（`conversation.composer.dock`）与账号中心（`main`）**没有父子关系**；
 *   · 四个页面（并行账号 / 添加账号 / 站点 / 盒子）如果各自 `fetch`，就会出现
 *     "站点页显示 3 个站点、账号页的站点下拉只有 2 个"这种**同一次操作后不一致**。
 *
 * 现在只有一条链路：**host 的 `/api/akso-workbench/state` 返回整份快照**（accounts+sites+boxes+revision），
 * 每次写操作 host 会重新拉 `/api/ui/state` 再把整份结果返回 ⇒ 这里用 `applyServer()` 整体替换，
 * 于是"看到的数据"永远来自同一个 revision。
 */
import { api, type ServerState } from './api.ts'
import { t } from './i18n.ts'

export type Status = 'loading' | 'ready' | 'error'

/** 设备流（浏览器授权）的前端状态；`deviceCode` 只在 host，浏览器只知道用户码与链接。 */
export interface DeviceState {
  userCode: string
  verificationUrl: string
  expiresIn: number
  interval: number
  status: 'waiting' | 'expired' | 'denied' | 'unknown'
}

export interface State extends Omit<ServerState, 'format'> {
  status: Status
  /** 加载/登录失败的原因（进「出错可重试」态） */
  error: string | null
  /** 一次性提示 */
  flash: string | null
  /** 正在执行的动作（行级 spinner / 防重复点击） */
  busy: string | null
  /** 表单提交中 */
  submitting: boolean
  /** 设备流进行中 */
  device: DeviceState | null
}

const initial: State = {
  status: 'loading',
  loggedIn: false,
  profile: null,
  revision: null,
  syncedAt: null,
  stale: false,
  syncError: null,
  accounts: [],
  sites: [],
  boxes: [],
  defaultBox: '',
  counts: { accounts: 0, sites: 0, boxes: 0 },
  currentAccountId: null,
  poolAccountIds: [],
  error: null,
  flash: null,
  busy: null,
  submitting: false,
  device: null,
}

let state: State = initial
const listeners = new Set<() => void>()
let refreshSeq = 0
let flashTimer: ReturnType<typeof setTimeout> | null = null

export function getState(): State {
  return state
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function emit(patch: Partial<State>): void {
  state = { ...state, ...patch }
  for (const listener of listeners) listener()
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

/** 服务端快照 → 本地状态（整体替换，不做合并，避免留下上一份的残影）。 */
function fromServer(next: ServerState): Partial<State> {
  return {
    status: 'ready',
    loggedIn: next.loggedIn,
    profile: next.profile,
    revision: next.revision,
    syncedAt: next.syncedAt,
    stale: next.stale,
    syncError: next.syncError,
    accounts: Array.isArray(next.accounts) ? next.accounts : [],
    sites: Array.isArray(next.sites) ? next.sites : [],
    boxes: Array.isArray(next.boxes) ? next.boxes : [],
    defaultBox: next.defaultBox ?? '',
    counts: next.counts ?? { accounts: 0, sites: 0, boxes: 0 },
    currentAccountId: next.currentAccountId ?? null,
    poolAccountIds: Array.isArray(next.poolAccountIds) ? next.poolAccountIds : [],
    error: null,
  }
}

/** 显示一条会自动消失的提示。 */
export function flash(text: string): void {
  if (flashTimer !== null) clearTimeout(flashTimer)
  emit({ flash: text })
  flashTimer = setTimeout(() => {
    flashTimer = null
    emit({ flash: null })
  }, 2600)
}

/** 首次加载 / 手动刷新。 */
export async function refresh(): Promise<void> {
  const seq = ++refreshSeq
  if (state.status !== 'ready') emit({ status: 'loading', error: null })
  try {
    const next = await api.state()
    if (seq !== refreshSeq) return
    emit(fromServer(next))
  } catch (error) {
    if (seq !== refreshSeq) return
    // 401 = 会话失效：host 已经清掉令牌，这里当作"未登录"而不是错误
    const text = message(error)
    if (/登录|401/.test(text)) {
      emit({ status: 'ready', loggedIn: false, profile: null, accounts: [], sites: [], boxes: [], error: null })
      return
    }
    emit({ status: 'error', error: text })
  }
}

export async function syncNow(): Promise<void> {
  emit({ busy: 'sync' })
  try {
    const next = await api.sync()
    emit(fromServer(next))
    if (next.syncError !== null) flash(next.syncError)
  } catch (error) {
    flash(t('page.flash.failed', { message: message(error) }))
  } finally {
    emit({ busy: null })
  }
}

/** 邮箱密码登录（备用路径；主路径是设备流）。 */
export async function login(email: string, password: string): Promise<boolean> {
  emit({ submitting: true })
  try {
    const next = await api.login({ email, password })
    emit({ ...fromServer(next), submitting: false, device: null })
    if (next.syncError !== null) flash(next.syncError)
    return true
  } catch (error) {
    emit({ submitting: false })
    flash(t('page.flash.failed', { message: message(error) }))
    return false
  }
}

/**
 * 设备流第一步：请 host 起一个授权请求（host 会顺手打开系统浏览器）。
 * 之后由组件负责按 `interval` 轮询 `pollDevice()`。
 */
export async function startDevice(): Promise<void> {
  emit({ busy: 'device-start' })
  try {
    const started = await api.deviceStart()
    emit({
      busy: null,
      device: {
        userCode: started.userCode,
        verificationUrl: started.verificationUrl,
        expiresIn: started.expiresIn,
        interval: Math.max(1, started.interval),
        status: 'waiting',
      },
      // 提示已经写在设备面板里了，不再往 flash（红色错误位）重复塞一条
      flash: null,
    })
  } catch (error) {
    emit({ busy: null })
    flash(t('page.flash.failed', { message: message(error) }))
  }
}

/**
 * 设备流轮询一次。
 * @returns true 表示已批准（state 已换成新快照），调用方应停止轮询
 */
export async function pollDevice(): Promise<boolean> {
  const current = state.device
  if (current === null || current.status !== 'waiting') return true
  try {
    const result = await api.devicePoll()
    if (result.status === 'approved' && result.state !== undefined) {
      emit({ ...fromServer(result.state), device: null })
      flash(t('cloud.flash.deviceOk'))
      return true
    }
    if (result.status === 'pending') return false
    emit({
      device: {
        ...current,
        status: result.status === 'expired' ? 'expired' : result.status === 'denied' ? 'denied' : 'unknown',
      },
    })
    return true
  } catch (error) {
    flash(t('page.flash.failed', { message: message(error) }))
    return true
  }
}

export function cancelDevice(): void {
  emit({ device: null })
}

export async function logout(): Promise<void> {
  emit({ busy: 'logout' })
  try {
    const next = await api.logout()
    emit({ ...fromServer(next), busy: null })
    flash(t('cloud.flash.loggedOut'))
  } catch (error) {
    emit({ busy: null })
    flash(t('page.flash.failed', { message: message(error) }))
  }
}

type WriteRunner = () => Promise<ServerState>

/** 所有写操作的统一外套：busy → 调接口 → 用返回的整份快照替换 → 提示。 */
async function write(label: string, run: WriteRunner, okText: string): Promise<boolean> {
  emit({ busy: label })
  try {
    const next = await run()
    emit({ ...fromServer(next), busy: null })
    flash(okText)
    return true
  } catch (error) {
    emit({ busy: null })
    flash(t('page.flash.failed', { message: message(error) }))
    return false
  }
}

export async function createAccount(input: {
  siteId: string
  username: string
  password: string
  tabName?: string
  note?: string
  box?: string
}): Promise<boolean> {
  emit({ submitting: true })
  const ok = await write('account-create', () => api.createAccount(input), t('page.flash.created', { name: input.username }))
  emit({ submitting: false })
  return ok
}

/** 把某个账号「连接」给 agent（本地偏好，乐观更新 + 失败回滚）。 */
export async function connectAccount(id: string): Promise<void> {
  if (state.currentAccountId === id || state.busy !== null) return
  const previous = state.currentAccountId
  const account = state.accounts.find((a) => a.id === id)
  const name = account === undefined ? id : (account.tabName || account.username)
  emit({ currentAccountId: id, busy: `connect:${id}` })
  try {
    const next = await api.setCurrent(id)
    emit({ ...fromServer(next), busy: null })
    flash(t('page.flash.connected', { name }))
  } catch (error) {
    emit({ currentAccountId: previous, busy: null })
    flash(t('page.flash.failed', { message: message(error) }))
  }
}

/** 清除连接：`currentAccountId = null`，界面回到「未连接」。 */
export async function disconnect(): Promise<void> {
  if (state.currentAccountId === null || state.busy !== null) return
  const previous = state.currentAccountId
  emit({ currentAccountId: null, busy: 'disconnect' })
  try {
    const next = await api.setCurrent('')
    emit({ ...fromServer(next), busy: null })
    flash(t('page.flash.disconnected'))
  } catch (error) {
    emit({ currentAccountId: previous, busy: null })
    flash(t('page.flash.failed', { message: message(error) }))
  }
}

export async function createSite(input: { name?: string; baseUrl: string; note?: string }): Promise<boolean> {
  emit({ submitting: true })
  const ok = await write('site-create', () => api.createSite(input), t('site.flash.created', { name: input.name ?? input.baseUrl }))
  emit({ submitting: false })
  return ok
}

export async function renameBox(source: string, target: string): Promise<boolean> {
  return write('box-rename', () => api.renameBox(source, target), t('box.flash.renamed', { source, target }))
}

export async function bindBoxSite(name: string, siteId: string): Promise<void> {
  await write(`box-bind:${name}`, () => api.bindBoxSite(name, siteId), t('box.flash.bound', { name }))
}

/** 配置池：把账号加入 / 移出 agent 可选登录的范围。 */
export async function togglePool(id: string, inPool: boolean): Promise<boolean> {
  const account = state.accounts.find((item) => item.id === id)
  const name = account === undefined ? id : (account.tabName || account.username)
  return write(
    `pool:${id}`,
    () => api.togglePool(id, inPool),
    inPool ? t('pool.flash.in', { name }) : t('pool.flash.out', { name }),
  )
}
