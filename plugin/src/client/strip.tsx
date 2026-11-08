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
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'

import { t } from './i18n.ts'
import { connectAccount, disconnect, flash, getState, refresh, subscribe } from './state.ts'
import { api, type CloudAccount, type EgmpTokenView, type PendingApprovalView } from './api.ts'

interface StripProps {
  t?: (key: string, vars?: Record<string, string | number>) => string
  /** 由 register 的 `inject` 注入：跳到账号中心页 */
  openPanel?: () => void
}

type Translate = (key: string, vars?: Record<string, string | number>) => string

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

/**
 * 对话里的审批窗（`m00603`：要弹在**对话区**，不是底部账号条）。
 *
 * 形态：`position: fixed` 居中遮罩 —— 覆盖在聊天消息流上方，视觉上就是"对话里弹出来的窗"。
 * 三个按钮对应三种结果：**同意**（唯一会写入的）、**拒绝**、**补充**（不写，把意见交回 agent）。
 * 窗口自己不提供 ×：审批只能显式做，点外面/按 Esc 都不关；宿主侧超时（8 分钟）或工具被取消时，
 * 轮询会发现 pending 消失，窗口自然收起。
 */
function ApprovalModal(props: { tr: Translate }) {
  const { tr } = props
  const [pending, setPending] = useState<PendingApprovalView | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [writing, setWriting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())

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

  if (pending === null) return null
  const report = pending.report
  const expired = pending.expiresAt <= now
  const total = report.steps.length

  const decide = async (verdict: 'approve' | 'deny' | 'supplement'): Promise<void> => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await api.configApprovalDecide(pending.id, verdict, verdict === 'supplement' ? note.trim() : undefined)
      setPending(null)
      setNote('')
      setWriting(false)
      flash(verdict === 'approve' ? tr('approval.approved') : verdict === 'deny' ? tr('approval.denied') : tr('approval.supplemented'))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="akw-approval" role="presentation">
      <div className="akw-approval__card" role="dialog" aria-modal="true" aria-label={tr('approval.title')}>
        <div className="akw-approval__head">
          <div>
            <b>{tr('approval.title')}</b>
            <span className="akw-approval__plan">{report.planId}</span>
          </div>
          <span className={`akw-approval__clock${expired ? ' is-off' : ''}`} title={tr('approval.expires')}>
            {expired ? tr('approval.expired') : countdownOf(pending.expiresAt - now)}
          </span>
        </div>

        <div className="akw-approval__meta">
          <div><span>{tr('approval.env')}</span><b>{report.env.trim() === '' ? '—' : report.env}</b></div>
          <div><span>{tr('approval.target')}</span><b>{report.host}</b></div>
          <div><span>{tr('approval.account')}</span><b>{report.account}</b></div>
        </div>

        <div className="akw-approval__summary">
          {tr('approval.summary', { create: report.summary.create, update: report.summary.update })}
          <span className="akw-approval__count">{tr('approval.stepCount', { count: total })}</span>
        </div>

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

        {writing ? (
          <textarea
            className="akw-approval__note"
            value={note}
            rows={3}
            placeholder={tr('approval.notePlaceholder')}
            onChange={(event) => setNote(event.target.value)}
          />
        ) : null}

        {error !== null ? <div className="akw-approval__error">{tr('approval.failed', { message: error })}</div> : null}

        <div className="akw-approval__foot">
          <span className="akw-approval__hint">{tr('approval.hint')}</span>
          <div className="akw-approval__buttons">
            {writing ? (
              <>
                <button type="button" className="akw-btn akw-btn--sm" disabled={busy} onClick={() => { setWriting(false); setNote('') }}>
                  {tr('approval.cancel')}
                </button>
                <button
                  type="button"
                  className="akw-btn akw-btn--sm akw-btn--primary"
                  disabled={busy || note.trim() === ''}
                  onClick={() => void decide('supplement')}
                >
                  {busy ? tr('approval.sending') : tr('approval.sendSupplement')}
                </button>
              </>
            ) : (
              <>
                <button type="button" className="akw-btn akw-btn--sm" disabled={busy || expired} onClick={() => void decide('deny')}>
                  {tr('approval.deny')}
                </button>
                <button type="button" className="akw-btn akw-btn--sm" disabled={busy || expired} onClick={() => setWriting(true)}>
                  {tr('approval.supplement')}
                </button>
                <button type="button" className="akw-btn akw-btn--sm akw-btn--primary" disabled={busy || expired} onClick={() => void decide('approve')}>
                  {busy ? tr('approval.sending') : tr('approval.approve')}
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
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

  if (state.status === 'loading') return <ApprovalModal tr={tr} />

  if (state.status === 'error') {
    return (
      <>
        <ApprovalModal tr={tr} />
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
      <ApprovalModal tr={tr} />
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
