/**
 * 账号条（`conversation.composer.dock`）。
 *
 * 位置由 CSS 决定（`order:99; margin-left:auto`）：宿主那一行是
 * `renderSlot('conversation.composer.dock')` → `ContextMeter`，slot 包裹层是 `display:contents`，
 * 所以这两个属性直接把本组件排到 **ContextMeter 右侧、整行最右**。
 *
 * 形态（2026-10-26 用户第 5–7 条）：
 *   · **只显示"已连接给 agent"的那一个账号**，内容为 `页签名 · 站点名`；
 *   · 已连接时**不显示 +**；未连接时只给一个按钮（有账号=「连接 Agent」，没账号=「添加账号」）；
 *   · 点已连接的胶囊 → 弹小面板：换账号 / 清除连接。
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

/** 还剩多久：未解出 exp 就返回空串，由调用方决定显示什么。 */
function remainingOf(expiresAt: number | null): { expired: boolean; value: string } {
  if (expiresAt === null) return { expired: false, value: '' }
  const left = expiresAt - Date.now()
  if (left <= 0) return { expired: true, value: '' }
  const minutes = Math.floor(left / 60000)
  if (minutes >= 60) return { expired: false, value: `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分` }
  if (minutes >= 1) return { expired: false, value: `${minutes} 分` }
  return { expired: false, value: `${Math.max(1, Math.floor(left / 1000))} 秒` }
}

/**
 * 令牌一角：**默认只显示"有没有、还剩多久"**，点「查看」才把 JWT 原文摊开。
 * 令牌存在 host 侧的 `egmp-session.json`，这里只是它的只读镜像 —— 页面刷新不会留任何副本。
 */
function TokenSection(props: { tr: Translate; view: EgmpTokenView | null; error: string | null }) {
  const { tr, view, error } = props
  const [reveal, setReveal] = useState(false)
  const [copied, setCopied] = useState(false)

  const token = view?.token ?? null
  const remain = remainingOf(view?.expiresAt ?? null)
  // 少于 5 分钟就换成警示色 —— 这一行本来就是拿来判断"要不要现在刷新"的
  const left = view?.expiresAt == null ? null : view.expiresAt - Date.now()
  const warn = left !== null && left > 0 && left < 5 * 60 * 1000

  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 1600)
    return () => window.clearTimeout(timer)
  }, [copied])

  const meta = (() => {
    if (error !== null) return error
    if (view === null) return tr('strip.token.loading')
    if (!view.loggedIn) return tr('strip.token.off')
    if (remain.expired) return `${view.account ?? ''} · ${tr('strip.token.expired')}`
    const left = remain.value === '' ? '' : ` · ${tr('strip.token.remaining', { value: remain.value })}`
    return `${view.account ?? ''}${left}`
  })()

  const tone = view?.loggedIn === true && !remain.expired && error === null
    ? (warn ? ' is-on is-warn' : ' is-on')
    : (remain.expired ? ' is-off' : '')

  return (
    <div className="akw-token">
      <div className={`akw-token__row${tone}`}>
        <span className="akw-token__dot" aria-hidden="true" />
        <span className="akw-token__name">{tr('strip.token.title')}</span>
        <span className="akw-token__meta" title={meta}>{meta}</span>
        {token !== null ? (
          <button type="button" className="akw-token__toggle" onClick={() => setReveal((value) => !value)}>
            {reveal ? tr('strip.token.hide') : tr('strip.token.show')}
          </button>
        ) : null}
      </div>
      {reveal && token !== null ? (
        <div className="akw-token__body">
          <div className="akw-token__times">
            <span>{tr('strip.token.issued', { value: clockOf(view?.issuedAt ?? null) })}</span>
            <span>{tr('strip.token.expires', { value: clockOf(view?.expiresAt ?? null) })}</span>
          </div>
          <pre className="akw-token__raw">{token}</pre>
          <div className="akw-token__acts">
            <button
              type="button"
              className="akw-btn akw-btn--sm"
              onClick={() => {
                void (async () => {
                  try {
                    await navigator.clipboard.writeText(token)
                    setCopied(true)
                  } catch {
                    setCopied(false)
                  }
                })()
              }}
            >
              {copied ? tr('strip.token.copied') : tr('strip.token.copy')}
            </button>
          </div>
        </div>
      ) : null}
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
      <TokenSection tr={tr} view={token} error={tokenError} />
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

  // 令牌只在面板打开时取一次（关掉就随组件留着，下次打开重取，免得看到过期的"剩余时间"）
  useEffect(() => {
    if (!open) return
    let alive = true
    setTokenError(null)
    void (async () => {
      try {
        const view = await api.configToken()
        if (alive) setToken(view)
      } catch (error) {
        if (!alive) return
        setToken(null)
        setTokenError(error instanceof Error ? error.message : String(error))
      }
    })()
    return () => {
      alive = false
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
