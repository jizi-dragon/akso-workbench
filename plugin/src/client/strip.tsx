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
 * 令牌一行：**只做展示** —— 有没有、签发到几点、到期到几点。
 * 令牌本体留在 host 的 `egmp-session.json` 里，接口只回时间戳（`token` 恒为 null），
 * 页面既不需要、也拿不到明文 —— 用户不用自己换票，也不该在这里把票抄走。
 */
function TokenMeta(props: { tr: Translate; view: EgmpTokenView | null; error: string | null }) {
  const { tr, view, error } = props
  const remain = remainingOf(view?.expiresAt ?? null)
  const left = view?.expiresAt == null ? null : view.expiresAt - Date.now()
  // 少于 5 分钟就换成警示色 —— 这一行本来就是拿来判断"要不要现在刷新"的
  const warn = left !== null && left > 0 && left < 5 * 60 * 1000

  const state = (() => {
    if (error !== null) return error
    if (view === null) return tr('strip.token.loading')
    if (!view.loggedIn) return tr('strip.token.off')
    if (remain.expired) return tr('strip.token.expired')
    return remain.value === '' ? tr('strip.token.on') : tr('strip.token.remaining', { value: remain.value })
  })()

  const tone = view?.loggedIn === true && !remain.expired && error === null
    ? (warn ? ' is-on is-warn' : ' is-on')
    : (remain.expired ? ' is-off' : '')

  return (
    <div className="akw-token">
      <div className={`akw-token__row${tone}`}>
        <span className="akw-token__dot" aria-hidden="true" />
        <span className="akw-token__name">{tr('strip.token.title')}</span>
        <span className="akw-token__meta" title={state}>{state}</span>
      </div>
      {view?.loggedIn === true ? (
        <div className="akw-token__times">
          {view.issuedAt !== null ? <span>{tr('strip.token.issued', { value: clockOf(view.issuedAt) })}</span> : null}
          <span>{tr('strip.token.expires', { value: clockOf(view.expiresAt) })}</span>
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
      <TokenMeta tr={tr} view={token} error={tokenError} />
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

  // 令牌是自动的：这里只负责读回来显示。每分钟问一次，过期时 host 会顺手换新票
  // （口令在 akso-cloud，页面从不接触明文，也没有"刷新令牌"按钮）。
  useEffect(() => {
    let alive = true
    const load = async (): Promise<void> => {
      try {
        const view = await api.configToken()
        if (!alive) return
        setToken(view)
        setTokenError(null)
      } catch (error) {
        if (!alive) return
        setToken(null)
        setTokenError(error instanceof Error ? error.message : String(error))
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), 60_000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  // 打开小面板时再取一次：看到的一定是当下这张票的剩余时间
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

  // 令牌状态的三个事实：有效（绿）· 快到期（黄，<5 分钟）· 过期/没有（红）
  const remain = remainingOf(token?.expiresAt ?? null)
  const jwtLeft = token?.expiresAt == null ? null : token.expiresAt - Date.now()
  const jwtValid = token?.loggedIn === true && !remain.expired && tokenError === null
  const jwtWarn = jwtValid && jwtLeft !== null && jwtLeft < 5 * 60 * 1000
  const jwtTone = jwtValid ? (jwtWarn ? ' is-warn' : ' is-ok') : (token === null && tokenError === null ? ' is-idle' : ' is-off')
  const jwtLabel = (() => {
    if (tokenError !== null) return tr('strip.jwt.error')
    if (token === null) return tr('strip.jwt.loading')
    if (token.loggedIn !== true) return tr('strip.jwt.none')
    if (remain.expired) return tr('strip.jwt.expired')
    return tr('strip.jwt.valid', { value: remain.value })
  })()
  const jwtTitle = (() => {
    if (tokenError !== null) return tokenError
    if (token?.expiresAt == null) return ''
    return `${clockOf(token.issuedAt)} 签发 · ${clockOf(token.expiresAt)} 到期（打开面板看详情）`
  })()

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
      <button
        type="button"
        className={`akw-strip__jwt${jwtTone}`}
        title={jwtTitle}
        aria-label={`eGMP 令牌：${jwtLabel}`}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="akw-strip__jwtDot" aria-hidden="true" />
        {jwtLabel}
      </button>
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
