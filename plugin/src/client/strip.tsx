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
import { connectAccount, disconnect, getState, refresh, subscribe } from './state.ts'
import { api, type CloudAccount, type EgmpTokenView } from './api.ts'

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

  useEffect(() => {
    void refresh()
  }, [])

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

  if (state.status === 'loading') return null

  if (state.status === 'error') {
    return (
      <div className="akw-strip">
        <button type="button" className="akw-strip__retry" title={state.error ?? ''} onClick={() => void refresh()}>
          {tr('strip.error')}
        </button>
      </div>
    )
  }

  const connected = state.accounts.find((account) => account.id === state.currentAccountId) ?? null
  // 只有进了「配置池」的账号才是 agent 可选的 —— 底部账号条的候选数量 = 配置池大小
  const pool = new Set(state.poolAccountIds)
  const poolAccounts = state.accounts.filter((account) => pool.has(account.id))

  return (
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
  )
}
