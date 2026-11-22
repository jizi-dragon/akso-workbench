/**
 * 账号条（`conversation.composer.dock`）。
 *
 * 位置由 CSS 决定（`order:99; margin-left:auto`）：宿主那一行是
 * `renderSlot('conversation.composer.dock')` → `ContextMeter`，slot 包裹层是 `display:contents`，
 * 所以这两个属性直接把本组件排到 **ContextMeter 右侧、整行最右**。
 *
 * 形态（2026-11-05 收窄）：
 *   · **只显示"已连接给 agent"的那一个账号**，内容为 `页签名 · 站点名`；
 *   · 已连接时不显示 +；未连接时只给一个按钮（有账号=「连接 Agent」，没账号=「添加账号」）；
 *   · 点已连接的胶囊 → 弹小面板：换账号 / 清除连接 / 令牌到期时间。
 *   · **不显示 JWT 原文** —— eGMP 令牌就是 JWT，换票由 host 自己处理；
 *     面板里只留一个「到期时间」，签发时间已按用户要求去掉（v4.2.54）。
 */
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'

import { t } from './i18n.ts'
import { connectAccount, disconnect, flash, getState, refresh, subscribe } from './state.ts'
import { api, type CloudAccount, type EgmpTokenView, type PendingApprovalView } from './api.ts'

interface SidebarRightLike {
  /** DSH 当前屏幕上挂载的 Session（observable 或裸值）。 */
  mounted?: unknown
  openResource?: (address: string, options?: Record<string, unknown>) => unknown
}

interface StripProps {
  t?: (key: string, vars?: Record<string, string | number>) => string
  /** 由 register 的 `inject` 注入：跳到账号中心页 */
  openPanel?: () => void
  /** 由 register 的 `inject` 注入：把审批文档打开到 DSH 右侧文件预览 */
  sidebarRight?: SidebarRightLike
}

type Translate = (key: string, vars?: Record<string, string | number>) => string

/** 业务摘要里资源种类（host 只给 kind，翻哪门语言由界面决定）。 */
const APPROVAL_KIND_KEY: Record<string, string> = {
  object: 'approval.kind.object',
  picklist: 'approval.kind.picklist',
  field: 'approval.kind.field',
  lifecycle: 'approval.kind.lifecycle',
  action: 'approval.kind.action',
  listLayout: 'approval.kind.listLayout',
  formLayout: 'approval.kind.formLayout',
  menu: 'approval.kind.menu',
  workflow: 'approval.kind.workflow',
  call: 'approval.kind.call',
}

/** 一行里最多念几个名字，再多就「等 N 项」——弹窗要一眼看完，不是清单。 */
const APPROVAL_NAME_LIMIT = 6

/** 概述分组首屏最多展示几类；其余折叠，保证底部按钮始终可见。 */
const APPROVAL_GROUP_LIMIT = 4

function accountLabel(account: CloudAccount): string {
  const head = account.tabName.trim() !== '' ? account.tabName : account.username
  const tail = account.siteName !== '' ? account.siteName : account.siteHost
  return tail === '' ? head : `${head} · ${tail}`
}

/** `19:07` —— 只给人看个大概，不跨天。 */
function clockOf(ms: number | null): string {
  if (ms === null) return '—'
  return new Date(ms).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

/** 还剩多久（只进 title，不占界面）。 */
function remainOf(expiresAt: number | null): string {
  if (expiresAt === null) return ''
  const left = expiresAt - Date.now()
  if (left <= 0) return ''
  const minutes = Math.floor(left / 60000)
  if (minutes >= 60) return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`
  if (minutes >= 1) return `${minutes} 分`
  return `${Math.max(1, Math.floor(left / 1000))} 秒`
}

/**
 * 令牌一行：只显示**到期时间**（v4.2.54 按用户要求：
 * 原先签发/到期两个时间并列，只保留一个；令牌原文与「查看」按钮不再出现）。
 * 剩余时长放 title，鼠标悬停才看；少于 5 分钟变警示色。
 */
function TokenLine(props: { tr: Translate; view: EgmpTokenView | null; error: string | null }) {
  const { tr, view, error } = props
  const expiresAt = view?.expiresAt ?? null
  const expired = expiresAt !== null && expiresAt <= Date.now()
  const warn = expiresAt !== null && !expired && expiresAt - Date.now() < 5 * 60 * 1000
  const text = error !== null
    ? tr('strip.token.error')
    : view === null
      ? tr('strip.token.loading')
      : !view.loggedIn
        ? tr('strip.token.off')
        : expired
          ? tr('strip.token.expired')
          : tr('strip.token.expires', { value: clockOf(expiresAt) })
  const title = expired ? '' : tr('strip.token.remaining', { value: remainOf(expiresAt) })
  return (
    <div className={`akw-token${expired ? ' is-off' : ''}${warn ? ' is-warn' : ''}`}>
      <span className="akw-token__dot" aria-hidden="true" />
      <span className="akw-token__name">{tr('strip.token.title')}</span>
      <span className="akw-token__time" title={title}>{text}</span>
    </div>
  )
}

/** `02:35` —— 审批窗剩余时间。 */
function countdownOf(ms: number): string {
  const left = Math.max(0, Math.ceil(ms / 1000))
  const minutes = Math.floor(left / 60)
  const seconds = left % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

interface MountedSessionInfo {
  id: string
  cwd?: string
}

function snapshotOf(value: unknown): unknown {
  if (typeof (value as { getSnapshot?: unknown } | undefined)?.getSnapshot === 'function') {
    return (value as { getSnapshot: () => unknown }).getSnapshot()
  }
  if (typeof (value as { get?: unknown } | undefined)?.get === 'function') {
    return (value as { get: () => unknown }).get()
  }
  return value
}

/** 从右侧栏服务取当前 Session；兼容 snapshot store、裸 id 和 Session 对象。 */
function mountedSessionInfo(service: SidebarRightLike | undefined): MountedSessionInfo | null {
  const mounted = service?.mounted
  const value = snapshotOf(mounted)
  const mountedRecord = (typeof value === 'string' ? { id: value } : value) as {
    id?: unknown
    sessionId?: unknown
    cwd?: unknown
    workspaceRoot?: unknown
    workspace?: { root?: unknown; cwd?: unknown }
  } | null | undefined
  const id = typeof mountedRecord?.id === 'string'
    ? mountedRecord.id
    : typeof mountedRecord?.sessionId === 'string' ? mountedRecord.sessionId : null
  if (id === null || id.trim() === '') return null
  const cwd = typeof mountedRecord?.cwd === 'string'
    ? mountedRecord.cwd
    : typeof mountedRecord?.workspaceRoot === 'string'
      ? mountedRecord.workspaceRoot
      : typeof mountedRecord?.workspace?.root === 'string'
        ? mountedRecord.workspace.root
        : typeof mountedRecord?.workspace?.cwd === 'string' ? mountedRecord.workspace.cwd : undefined
  return { id, ...(cwd === undefined ? {} : { cwd }) }
}

/** 把工作区绝对路径转成 DSH 文档预览地址；有 cwd 时优先转相对路径。 */
function reviewAddress(path: string, sessionId: string, cwd?: string): string {
  let normalized = path.replaceAll('\\', '/')
  if (cwd !== undefined) {
    const root = cwd.replaceAll('\\', '/').replace(/\/+$/, '')
    if (root !== '' && normalized.toLowerCase().startsWith(`${root.toLowerCase()}/`)) {
      normalized = normalized.slice(root.length + 1)
    }
  }
  /* 审核文件按约定位于工作区 `akso-workbench-data/reviews/`。DSH 文档预览按
   * Session 根解析相对路径更稳；即使拿不到 cwd，也能从路径里切出这一段，
   * 避免把 `D:/...` 绝对路径交给文件服务后打不开。 */
  const marker = '/akso-workbench-data/reviews/'
  const at = normalized.toLowerCase().indexOf(marker)
  if (at >= 0) normalized = normalized.slice(at + 1)
  return `dsh-resource://file/session/${encodeURIComponent(sessionId)}/${encodeURI(normalized)}`
}

/**
 * 审批窗不能按整个 viewport 定位：DSH 左右侧栏开合时，只有中间会话区才是
 * 合法范围。`.akw-strip` 本身只是账号条里的一小段，不能拿它的宽度当会话区；
 * 向上找第一个“像输入区/会话栏”的祖先（>=360px 且不是整页根节点）。
 */
function approvalAnchorElement(): Element | null {
  const strip = document.querySelector('.akw-strip')
  if (strip === null) return null
  const viewportW = window.innerWidth
  const minWidth = Math.min(360, Math.max(240, viewportW - 24))
  let fallback: Element | null = null
  let element: Element | null = strip.parentElement
  for (let depth = 0; element !== null && depth < 8; depth += 1) {
    const rect = element.getBoundingClientRect()
    if (rect.width >= minWidth && rect.height >= 32) {
      if (rect.width <= viewportW - 24) return element
      fallback = fallback ?? element
    }
    element = element.parentElement
  }
  return fallback
}

function approvalAnchorRect(): DOMRect | null {
  return approvalAnchorElement()?.getBoundingClientRect() ?? null
}

/**
 * 对话里的审批窗（`m00603`：要弹在**对话区**，不是底部账号条）。
 *
 * 形态：`position: fixed` 居中遮罩 —— 覆盖在聊天消息流上方，视觉上就是"对话里弹出来的窗"。
 * 三个按钮对应三种结果：**同意**（唯一会写入的）、**拒绝**、**补充**（不写，把意见交回 agent）。
 * 窗口自己不提供 ×：审批只能显式做，点外面/按 Esc 都不关；宿主侧超时（8 分钟）或工具被取消时，
 * 轮询会发现 pending 消失，窗口自然收起。
 */
function ApprovalModal(props: {
  tr: Translate
  sidebarRight?: SidebarRightLike
}) {
  const { tr } = props
  const [pending, setPending] = useState<PendingApprovalView | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  /** 审批卡片是 composer 的兄弟节点，portal 后不能再用 viewport 居中：
   *  要同时让出输入框（bottom）和左右侧边栏（left/right），只覆盖会话区。 */
  const [layout, setLayout] = useState({ bottom: 120, left: 12, right: 12 })
  /** 允许折叠成一条标题栏，避免整个审批窗挡住对话。 */
  const [collapsed, setCollapsed] = useState(false)
  /** 概述默认只展示前几类，完整清单在审核文档里。 */
  const [showAllBusiness, setShowAllBusiness] = useState(false)

  useEffect(() => {
    let alive = true
    const load = async (): Promise<void> => {
      try {
        const result = await api.configApprovalPending()
        if (!alive) return
        setPending(result.pending)
        setError(null)
      } catch (error) {
        // host 还没起来 / 页面正在卸载：不打扰界面，但留一条 debug 便于排查
        console.debug('[akso-workbench] 审批轮询失败，下一轮重试', error)
      }
    }
    void load()
    const poll = window.setInterval(() => void load(), 2000)
    const clock = window.setInterval(() => setNow(Date.now()), 1000)
    return () => {
      alive = false
      window.clearInterval(poll)
      window.clearInterval(clock)
    }
  }, [])

  /* composer 会把 fixed 子元素限制在自己的层里；portal 到 body 后必须自己
   * 量一下账号条：底部抬到输入区上方，左右让出侧边栏，只覆盖会话区。
   * 侧边栏开合、窗口 resize、visualViewport 滚动都会改变这个矩形。 */
  useLayoutEffect(() => {
    if (pending === null) return
    let frame = 0
    let lastKey = ''
    const measure = (): void => {
      window.cancelAnimationFrame(frame)
      frame = window.requestAnimationFrame(() => {
        const rect = approvalAnchorRect()
        const viewportW = window.innerWidth
        const viewportH = window.visualViewport?.height ?? window.innerHeight
        if (rect === null || !Number.isFinite(rect.top) || rect.top <= 0 || rect.width <= 0) {
          const fallback = {
            bottom: Math.max(96, Math.round(viewportH * 0.16)),
            left: 12,
            right: 12,
          }
          const key = `${fallback.bottom}/${fallback.left}/${fallback.right}`
          if (key !== lastKey) {
            lastKey = key
            setLayout(fallback)
          }
          return
        }
        const bottom = Math.max(16, Math.min(Math.round(viewportH - 16), Math.round(window.innerHeight - rect.top + 14)))
        const left = Math.max(12, Math.min(Math.round(viewportW / 2), Math.round(rect.left)))
        const right = Math.max(12, Math.min(Math.round(viewportW / 2), Math.round(viewportW - rect.right)))
        const key = `${bottom}/${left}/${right}`
        if (key !== lastKey) {
          lastKey = key
          setLayout({ bottom, left, right })
        }
      })
    }
    measure()
    const anchor = approvalAnchorElement()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    if (observer !== null) {
      if (anchor !== null) observer.observe(anchor)
      observer.observe(document.body)
    }
    window.addEventListener('resize', measure)
    window.visualViewport?.addEventListener('resize', measure)
    window.visualViewport?.addEventListener('scroll', measure)
    document.addEventListener('transitionend', measure, true)
    // 侧边栏开合/输入区变高不一定触发 resize；轻量轮询保证位置在一帧内跟上。
    const settle = window.setInterval(measure, 250)
    return () => {
      window.cancelAnimationFrame(frame)
      window.clearInterval(settle)
      observer?.disconnect()
      window.removeEventListener('resize', measure)
      window.visualViewport?.removeEventListener('resize', measure)
      window.visualViewport?.removeEventListener('scroll', measure)
      document.removeEventListener('transitionend', measure, true)
    }
  }, [pending])

  if (pending === null) return null
  const report = pending.report
  const expired = pending.expiresAt <= now
  const total = report.steps.length
  /* 旧 host 还没有 business 字段：降级成「只有逐条接口」，不要白屏。 */
  const business = Array.isArray(report.business) ? report.business : []
  const review = report.review
  const openReview = (): void => {
    if (review === undefined) return
    const mounted = mountedSessionInfo(props.sidebarRight)
    const sessionId = review.sessionId ?? mounted?.id
    const address = review.url
      ?? (sessionId === null || sessionId === undefined ? null : reviewAddress(review.path, sessionId, mounted?.cwd))
    if (address === null || typeof props.sidebarRight?.openResource !== 'function') {
      flash(tr('approval.reviewUnavailable'))
      return
    }
    try {
      void props.sidebarRight.openResource(address, { revealIfOpened: true })
    } catch (error) {
      flash(tr('approval.reviewFailed', { message: error instanceof Error ? error.message : String(error) }))
    }
  }

  const decide = async (verdict: 'approve' | 'deny' | 'supplement'): Promise<void> => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await api.configApprovalDecide(pending.id, verdict, verdict === 'supplement' ? note.trim() : undefined)
      setPending(null)
      setNote('')
      flash(verdict === 'approve' ? tr('approval.approved') : verdict === 'deny' ? tr('approval.denied') : tr('approval.adjusted'))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const visibleBusiness = showAllBusiness ? business : business.slice(0, APPROVAL_GROUP_LIMIT)
  const hiddenBusiness = business.length - visibleBusiness.length
  const overlayStyle = {
    '--akw-approval-bottom': `${layout.bottom}px`,
    '--akw-approval-left': `${layout.left}px`,
    '--akw-approval-right': `${layout.right}px`,
  } as CSSProperties
  return createPortal(
    <div className="akw-approval" role="presentation" style={overlayStyle}>
      <div className="akw-approval__card" role="dialog" aria-modal="true" aria-label={tr('approval.title')}>
        <div className="akw-approval__head">
          <div>
            <b>{tr('approval.title')}</b>
            <span className="akw-approval__plan">{report.planId}</span>
          </div>
          <div className="akw-approval__headActions">
            <button
              type="button"
              className="akw-approval__collapse"
              aria-expanded={!collapsed}
              title={tr(collapsed ? 'approval.expand' : 'approval.collapse')}
              onClick={() => setCollapsed((value) => !value)}
            >
              {tr(collapsed ? 'approval.expand' : 'approval.collapse')}
            </button>
            <span className={`akw-approval__clock${expired ? ' is-off' : ''}`} title={tr('approval.expires')}>
              {expired ? tr('approval.expired') : countdownOf(pending.expiresAt - now)}
            </span>
          </div>
        </div>

        {!collapsed ? (
          <>
        <div className="akw-approval__body">
        <div className="akw-approval__meta">
          <div><span>{tr('approval.env')}</span><b>{report.env.trim() === '' ? '—' : report.env}</b></div>
          <div><span>{tr('approval.target')}</span><b>{report.host}</b></div>
          <div><span>{tr('approval.account')}</span><b>{report.account}</b></div>
        </div>

        {business.length > 0 ? (
          <div className="akw-approval__business">
            <div className="akw-approval__businessHead">{tr('approval.business')}</div>
            <ul className="akw-approval__businessList">
              {visibleBusiness.map((group) => {
                const shown = group.names.slice(0, APPROVAL_NAME_LIMIT)
                const rest = group.names.length - shown.length
                return (
                  <li key={`${group.action}-${group.kind}`}>
                    <span className={`akw-approval__verb is-${group.action}`}>
                      {group.action === 'create' ? tr('approval.create') : tr('approval.update')}
                    </span>
                    <b className="akw-approval__kind">{tr(APPROVAL_KIND_KEY[group.kind] ?? group.kind)}</b>
                    <span className="akw-approval__names">
                      {shown.join(tr('approval.listSeparator'))}
                      {rest > 0 ? tr('approval.moreNames', { count: rest }) : ''}
                    </span>
                  </li>
                )
              })}
            </ul>
            {business.length > APPROVAL_GROUP_LIMIT ? (
              <button
                type="button"
                className="akw-approval__more"
                onClick={() => setShowAllBusiness((value) => !value)}
              >
                {showAllBusiness
                  ? tr('approval.showFewerGroups')
                  : tr('approval.showAllGroups', { count: hiddenBusiness })}
              </button>
            ) : null}
          </div>
        ) : null}

        {report.summary.irreversible > 0 ? (
          <div className="akw-approval__summary is-warn">
            {tr('approval.irreversibleCount', { count: report.summary.irreversible })}
          </div>
        ) : null}

        {review !== undefined ? (
          <div className="akw-approval__review">
            <div className="akw-approval__reviewHead">
              <span>
                {tr('approval.reviewTitle')}
                <em className={`akw-approval__reviewKind is-${review.kind}`}>
                  {tr(review.kind === 'update' ? 'approval.reviewKindUpdate' : 'approval.reviewKindCreate')}
                </em>
              </span>
              <button type="button" className="akw-btn akw-btn--sm" onClick={openReview}>
                {tr('approval.openReview')}
              </button>
            </div>
            <div className="akw-approval__reviewTitle">{review.title}</div>
            <div className="akw-approval__reviewSection">
              <b>{tr('approval.taskOverview')}</b>
              <p>{review.problem}</p>
            </div>
            <div className="akw-approval__reviewMeta">
              {tr('approval.reviewExpectedCount', { count: review.expected.length })}
            </div>
            <div className="akw-approval__reviewPath" title={review.path}>{review.path}</div>
          </div>
        ) : null}

        {review === undefined ? (
          <details className="akw-approval__details">
            <summary>{tr('approval.showSteps', { count: total })}</summary>
            <div className="akw-approval__steps">
              {report.steps.map((step) => (
                <div className="akw-approval__step" key={`${step.index}-${step.path}`}>
                  <div className="akw-approval__stepHead">
                    <b>{step.index}. {step.title}</b>
                    <span className={`akw-approval__action is-${step.action}`}>{step.action === 'create' ? tr('approval.create') : tr('approval.update')}</span>
                  </div>
                  <code className="akw-approval__endpoint">{step.method} {step.path}</code>
                  <pre className="akw-approval__params">{JSON.stringify(step.params, null, 2)}</pre>
                  {step.irreversible || step.risk === 'high' || step.warnings.length > 0 ? (
                    <div className="akw-approval__warn">
                      {step.irreversible ? <span className="akw-approval__tag is-bad">{tr('approval.irreversible')}</span> : null}
                      {step.risk === 'high' ? <span className="akw-approval__tag is-bad">{tr('approval.riskHigh')}</span> : null}
                      {step.warnings.map((warning) => <span className="akw-approval__tag" key={warning}>{warning}</span>)}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          </details>
        ) : null}

        <label className="akw-approval__adjust">
          <span>{tr('approval.adjustLabel')}</span>
          <textarea
            className="akw-approval__note"
            value={note}
            rows={3}
            placeholder={tr('approval.notePlaceholder')}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>

        {error !== null ? <div className="akw-approval__error">{tr('approval.failed', { message: error })}</div> : null}
        </div>

        <div className="akw-approval__foot">
          <span className="akw-approval__hint">
            {note.trim() !== '' ? tr('approval.adjustHint') : tr('approval.hint')}
          </span>
          <div className="akw-approval__buttons">
            <button type="button" className="akw-btn akw-btn--sm" disabled={busy || expired} onClick={() => void decide('deny')}>
              {tr('approval.deny')}
            </button>
            <button
              type="button"
              className="akw-btn akw-btn--sm"
              disabled={busy || expired || note.trim() === ''}
              onClick={() => void decide('supplement')}
            >
              {busy ? tr('approval.sending') : tr('approval.submitAdjust')}
            </button>
            <button
              type="button"
              className="akw-btn akw-btn--sm akw-btn--primary"
              disabled={busy || expired || note.trim() !== ''}
              onClick={() => void decide('approve')}
            >
              {busy ? tr('approval.sending') : tr('approval.approve')}
            </button>
          </div>
        </div>
          </>
        ) : (
          <div className="akw-approval__collapsed">
            {tr('approval.collapsedHint')}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}

function Picker(props: {
  tr: Translate
  accounts: CloudAccount[]
  current: string | null
  token: EgmpTokenView | null
  tokenError: string | null
  onClose: () => void
  onAdd: () => void
}) {
  const { tr, accounts, current, token, tokenError, onClose, onAdd } = props
  return (
    <div className="akw-picker" role="dialog" aria-label={tr('strip.picker.title')}>
      <div className="akw-picker__head">
        <b>{tr('strip.picker.title')}</b>
        <button type="button" className="akw-picker__x" aria-label={tr('strip.picker.close')} onClick={onClose}>×</button>
      </div>
      {accounts.length === 0 ? (
        <div className="akw-picker__empty">
          <p>{tr('strip.picker.empty')}</p>
          <button type="button" className="akw-btn akw-btn--sm akw-btn--primary" onClick={onAdd}>
            {tr('strip.add')}
          </button>
        </div>
      ) : (
        <ul className="akw-picker__list">
          {accounts.map((account) => {
            const isOn = account.id === current
            return (
              <li key={account.id}>
                <button
                  type="button"
                  className={`akw-picker__item${isOn ? ' is-on' : ''}`}
                  aria-current={isOn ? 'true' : undefined}
                  onClick={() => {
                    void connectAccount(account.id)
                    onClose()
                  }}
                >
                  <span className="akw-picker__name">{account.tabName.trim() !== '' ? account.tabName : account.username}</span>
                  <span className="akw-picker__site">{account.siteName !== '' ? account.siteName : account.siteHost}</span>
                  {isOn ? <span className="akw-picker__tick" aria-hidden="true">✓</span> : null}
                </button>
              </li>
            )
          })}
        </ul>
      )}
      <TokenLine tr={tr} view={token} error={tokenError} />
      {current !== null ? (
        <button
          type="button"
          className="akw-picker__clear"
          onClick={() => {
            void disconnect()
            onClose()
          }}
        >
          {tr('strip.picker.clear')}
        </button>
      ) : null}
    </div>
  )
}

export function AccountStrip(props: StripProps = {}) {
  const tr = typeof props.t === 'function' ? props.t : t
  const state = useSyncExternalStore(subscribe, getState, getState)
  const [open, setOpen] = useState(false)
  const [token, setToken] = useState<EgmpTokenView | null>(null)
  const [tokenError, setTokenError] = useState<string | null>(null)
  const rootRef = useRef<HTMLDivElement | null>(null)

  // 只有进了「配置池」的账号才是 agent 可选的 —— 账号条的候选数量 = 配置池大小
  const pool = new Set(state.poolAccountIds)
  const poolAccounts = state.accounts.filter((account) => pool.has(account.id))
  const connected = state.accounts.find((account) => account.id === state.currentAccountId) ?? null

  useEffect(() => {
    void refresh()
  }, [])

  // 工具想写但没有账号：自动把「选账号」面板弹出来；池子为空就引导去添加账号
  useEffect(() => {
    if (!state.needsAccount || open) return
    if (poolAccounts.length === 0) {
      flash(tr('strip.needsAccount'))
      props.openPanel?.()
      return
    }
    setOpen(true)
  }, [state.needsAccount, state.needsAccountSince])

  // 面板打开时才问令牌状态，30s 一次就够（只显示到期时间，过期由 host 自动续）
  useEffect(() => {
    if (!open) return
    let alive = true
    const load = async (): Promise<void> => {
      try {
        const view = await api.configToken()
        if (alive) {
          setToken(view)
          setTokenError(null)
        }
      } catch (err) {
        if (alive) setTokenError(err instanceof Error ? err.message : String(err))
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), 30_000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [open])

  // 点外面 / Esc 关闭小面板
  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent): void => {
      if (rootRef.current !== null && !rootRef.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (state.status === 'loading') return <ApprovalModal tr={tr} sidebarRight={props.sidebarRight}  />

  if (state.status === 'error') {
    return (
      <>
        <ApprovalModal tr={tr} sidebarRight={props.sidebarRight}  />
        <div className="akw-strip">
          <button type="button" className="akw-strip__retry" title={state.error ?? ''} onClick={() => void refresh()}>
            {tr('strip.error')}
          </button>
        </div>
      </>
    )
  }

  return (
    <>
      <ApprovalModal tr={tr} sidebarRight={props.sidebarRight}  />
      <div className="akw-strip" ref={rootRef}>
      {connected !== null ? (
        <button
          type="button"
          className="akw-connected"
          aria-current="true"
          title={tr('strip.editHint')}
          onClick={() => setOpen((value) => !value)}
        >
          {accountLabel(connected)}
        </button>
      ) : (
        <button
          type="button"
          className="akw-strip__add"
          onClick={() => {
            if (poolAccounts.length === 0) props.openPanel?.()
            else setOpen(true)
          }}
        >
          {poolAccounts.length === 0 ? tr('strip.add') : tr('strip.connect')}
        </button>
      )}
      {open ? (
        <Picker
          tr={tr}
          accounts={poolAccounts}
          current={state.currentAccountId}
          token={token}
          tokenError={tokenError}
          onClose={() => setOpen(false)}
          onAdd={() => {
            setOpen(false)
            props.openPanel?.()
          }}
        />
      ) : null}
      </div>
    </>
  )
}
