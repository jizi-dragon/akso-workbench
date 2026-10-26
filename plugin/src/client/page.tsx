/**
 * 账号中心页（`main` 座位，key = `akso-workbench`）。
 *
 * 版式学 akso-pass 的自有页面：**顶部**品牌条（标题 + 统计 + 同步状态 + 主操作）
 * → 下划线式 tab（并行账号 / 添加账号 / 站点管理 / 盒子管理）→ 内容区自行滚动。
 *
 * ★ 数据一致性：四个 tab 与账号条读的是**同一个 store / 同一份 host 快照**，
 *   任何写操作都由 host 回拉 `/api/ui/state` 后整体替换 —— 页内不维护第二份数据。
 * ★ 未登录时整页是登录门（akso-pass 的 login-mask 形态），登录后才渲染四个 tab。
 */
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'

import { t } from './i18n.ts'
import {
  bindBoxSite,
  cancelDevice,
  connectAccount,
  createAccount,
  createSite,
  getState,
  login,
  logout,
  pollDevice,
  refresh,
  renameBox,
  startDevice,
  subscribe,
  syncNow,
} from './state.ts'
import { ConfigToolsTab } from './tools.tsx'

type Tab = 'accounts' | 'add' | 'sites' | 'boxes'

const ACCOUNT_EMPTY = { siteId: '', username: '', password: '', tabName: '', box: '', note: '' }
const SITE_EMPTY = { name: '', baseUrl: '', note: '' }
const LOGIN_EMPTY = { email: '', password: '' }

function useStore() {
  return useSyncExternalStore(subscribe, getState, getState)
}

function fmtTime(ms: number | null): string {
  if (ms === null) return '—'
  return new Date(ms).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

/** 每 30s 触发一次重渲染：让「同步于 N 分钟前」这类相对时间自己走。 */
function useNow(intervalMs = 30000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(timer)
  }, [intervalMs])
  return now
}

function fmtRelative(ms: number, now: number): string {
  const diff = Math.max(0, now - ms)
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  return new Date(ms).toLocaleDateString('zh-CN')
}

const menuItemStyle: React.CSSProperties = {
  display: 'block',
  width: '100%',
  textAlign: 'left',
  padding: '7px 10px',
  border: 0,
  borderRadius: 7,
  background: 'transparent',
  color: 'inherit',
  font: 'inherit',
  fontSize: 13,
  cursor: 'pointer',
}
/* ────────────────────────────── 登录门 ────────────────────────────── */

function LoginGate({ tr, embedded = false }: { tr: (key: string, vars?: Record<string, string | number>) => string; embedded?: boolean }) {
  const state = useStore()
  const [form, setForm] = useState(LOGIN_EMPTY)
  const device = state.device

  // 设备流轮询：批准前每 interval 秒问一次；条件变化/卸载即停
  useEffect(() => {
    if (device === null || device.status !== 'waiting') return
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = async (): Promise<void> => {
      if (stopped) return
      const done = await pollDevice()
      if (!done && !stopped) timer = setTimeout(() => void tick(), device.interval * 1000)
    }
    timer = setTimeout(() => void tick(), device.interval * 1000)
    return () => {
      stopped = true
      if (timer !== undefined) clearTimeout(timer)
    }
  }, [device?.status, device?.userCode, device?.interval])

  async function submitPassword(event: React.FormEvent): Promise<void> {
    event.preventDefault()
    if (form.email.trim() === '' || form.password === '' || state.submitting) return
    const ok = await login(form.email.trim(), form.password)
    if (ok) setForm(LOGIN_EMPTY)
  }

  const body = (
    <div className="akw-body akw-body--center">
        <section className="akw-login">
          <h2 className="akw-login__title">{tr('cloud.gate.title')}</h2>
          <p className="akw-login__desc">{tr('cloud.gate.desc')}</p>

          {device === null ? (
            <button
              type="button"
              className="akw-btn akw-btn--primary akw-login__primary"
              disabled={state.busy === 'device-start'}
              onClick={() => void startDevice()}
            >
              {state.busy === 'device-start' ? tr('cloud.device.starting') : tr('cloud.device.action')}
            </button>
          ) : (
            <div className="akw-device">
              <div className="akw-device__code">
                <span>{tr('cloud.device.code')}</span>
                <b>{device.userCode}</b>
              </div>
              {device.status === 'waiting' ? <p className="akw-login__hint">{tr('cloud.device.waiting')}</p> : null}
              {device.status === 'expired' ? <p className="akw-login__err">{tr('cloud.device.expired')}</p> : null}
              {device.status === 'denied' ? <p className="akw-login__err">{tr('cloud.device.denied')}</p> : null}
              {device.status === 'unknown' ? <p className="akw-login__err">{tr('cloud.device.unknown')}</p> : null}
              <div className="akw-device__actions">
                <button
                  type="button"
                  className="akw-btn akw-btn--sm"
                  onClick={() => window.open(device.verificationUrl, '_blank', 'noopener')}
                >
                  {tr('cloud.device.openAgain')}
                </button>
                <button type="button" className="akw-btn akw-btn--sm akw-btn--ghost" onClick={() => cancelDevice()}>
                  {tr('page.action.cancel')}
                </button>
              </div>
            </div>
          )}

          <p className="akw-login__hint">{tr('cloud.gate.hint')}</p>
          {state.flash !== null ? <p className="akw-login__err" role="alert">{state.flash}</p> : null}

          <details className="akw-login__adv">
            <summary>{tr('cloud.device.fallback')}</summary>
            <form className="akw-login__fallback" onSubmit={(event) => void submitPassword(event)}>
              <label className="akw-field">
                <span className="akw-field__label">{tr('cloud.gate.email')}</span>
                <input
                  className="akw-input"
                  type="email"
                  autoComplete="username"
                  value={form.email}
                  placeholder="you@example.com"
                  onChange={(event) => setForm({ ...form, email: event.target.value })}
                />
              </label>
              <label className="akw-field">
                <span className="akw-field__label">{tr('cloud.gate.password')}</span>
                <input
                  className="akw-input"
                  type="password"
                  autoComplete="current-password"
                  value={form.password}
                  onChange={(event) => setForm({ ...form, password: event.target.value })}
                />
              </label>
              <button
                type="submit"
                className="akw-btn"
                disabled={state.submitting || form.email.trim() === '' || form.password === ''}
              >
                {state.submitting ? tr('cloud.gate.submitting') : tr('cloud.gate.submit')}
              </button>
            </form>
          </details>
        </section>
    </div>
  )

  if (embedded) return body

  return (
    <div className="akw-page">
      <header className="akw-topbar">
        <div className="akw-topbar__left">
          <span className="akw-mark" aria-hidden="true">a</span>
          <div className="akw-brandtext">
            <h1>{tr('page.title')}</h1>
            <p>{tr('page.subtitle')}</p>
          </div>
        </div>
      </header>
      {body}
    </div>
  )
}

/* ────────────────────────────── 并行账号 ────────────────────────────── */

function AccountsTab({ tr, query }: { tr: (key: string, vars?: Record<string, string | number>) => string; query: string }) {
  const state = useStore()

  const q = query.trim().toLowerCase()
  const matched = q === ''
    ? state.accounts
    : state.accounts.filter((account) => [account.tabName, account.username, account.siteName, account.siteHost, account.note, account.box]
      .some((field) => String(field ?? '').toLowerCase().includes(q)))

  if (matched.length === 0) {
    return (
      <div className="akw-empty">
        <span className="akw-empty__icon" aria-hidden="true">◍</span>
        <span className="akw-empty__title">{state.accounts.length === 0 ? tr('page.empty.title') : tr('page.filtered.title')}</span>
        <span className="akw-empty__desc">{state.accounts.length === 0 ? tr('page.empty.desc') : tr('page.filtered.desc')}</span>
      </div>
    )
  }

  return (
    <ul className="akw-grid">
      {matched.map((account) => {
        const isConnected = account.id === state.currentAccountId
        const connecting = state.busy === `connect:${account.id}`
        const title = account.tabName.trim() !== '' ? account.tabName : account.username
        const site = account.siteName !== '' ? account.siteName : account.siteHost
        return (
          <li key={account.id} className={`akw-card${isConnected ? ' is-connected' : ''}`}>
            <div className="akw-card__head">
              <span className="akw-avatar" aria-hidden="true">{title.slice(0, 1).toUpperCase()}</span>
              <div className="akw-card__id">
                <b title={title}>{title}</b>
                <span title={site || account.baseUrl}>{site === '' ? '—' : site}</span>
              </div>
              {isConnected ? <span className="akw-badge">{tr('page.tag.connected')}</span> : null}
            </div>
            <dl className="akw-facts">
              <div>
                <dt>{tr('page.card.username')}</dt>
                <dd title={account.username}>{account.username}</dd>
              </div>
              <div>
                <dt>{tr('page.card.box')}</dt>
                <dd title={account.box}>{account.box || state.defaultBox || '—'}</dd>
              </div>
            </dl>
            {account.note !== '' ? <p className="akw-card__note">{account.note}</p> : null}
            <div className="akw-card__actions">
              {isConnected ? (
                <span className="akw-card__hint">{tr('page.tag.connectedHint')}</span>
              ) : (
                <button
                  type="button"
                  className="akw-btn akw-btn--sm"
                  disabled={state.busy !== null}
                  onClick={() => void connectAccount(account.id)}
                >
                  {connecting ? tr('page.action.connecting') : tr('page.action.connect')}
                </button>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}

/* ────────────────────────────── 添加账号 ────────────────────────────── */

function AddAccountTab({ tr }: { tr: (key: string, vars?: Record<string, string | number>) => string }) {
  const state = useStore()
  const [form, setForm] = useState(ACCOUNT_EMPTY)

  useEffect(() => {
    // 进来时把站点默认选上：只有一个站点时省一次点击
    if (form.siteId === '' && state.sites.length === 1) setForm({ ...form, siteId: state.sites[0]!.id })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.sites.length])

  if (state.sites.length === 0) {
    return (
      <div className="akw-empty">
        <span className="akw-empty__title">{tr('site.empty.title')}</span>
        <span className="akw-empty__desc">{tr('site.empty.desc')}</span>
      </div>
    )
  }

  const canSubmit = form.siteId !== '' && form.username.trim() !== '' && form.password !== '' && !state.submitting

  async function submit(event: React.FormEvent): Promise<void> {
    event.preventDefault()
    if (!canSubmit) return
    const ok = await createAccount({
      siteId: form.siteId,
      username: form.username.trim(),
      password: form.password,
      tabName: form.tabName.trim() || undefined,
      box: form.box.trim() || undefined,
      note: form.note.trim() || undefined,
    })
    if (ok) setForm({ ...ACCOUNT_EMPTY, siteId: form.siteId })
  }

  return (
    <form className="akw-form" onSubmit={(event) => void submit(event)}>
      <div className="akw-form__head">
        <h2>{tr('page.form.title')}</h2>
        <span>{tr('page.form.desc')}</span>
      </div>
      <label className="akw-field">
        <span className="akw-field__label">{tr('page.form.site')}</span>
        <select className="akw-input" value={form.siteId} onChange={(event) => setForm({ ...form, siteId: event.target.value })}>
          <option value="">{tr('page.form.site.ph')}</option>
          {state.sites.map((site) => (
            <option key={site.id} value={site.id}>{site.name || site.baseUrl}</option>
          ))}
        </select>
      </label>
      <label className="akw-field">
        <span className="akw-field__label">{tr('page.form.username')}</span>
        <input
          className="akw-input"
          value={form.username}
          placeholder={tr('page.form.username.ph')}
          autoComplete="off"
          onChange={(event) => setForm({ ...form, username: event.target.value })}
        />
      </label>
      <label className="akw-field">
        <span className="akw-field__label">{tr('page.form.password')}</span>
        <input
          className="akw-input"
          type="password"
          value={form.password}
          placeholder={tr('page.form.password.ph')}
          autoComplete="new-password"
          onChange={(event) => setForm({ ...form, password: event.target.value })}
        />
      </label>
      <label className="akw-field">
        <span className="akw-field__label">{tr('page.form.tabName')}</span>
        <input
          className="akw-input"
          value={form.tabName}
          placeholder={tr('page.form.tabName.ph')}
          onChange={(event) => setForm({ ...form, tabName: event.target.value })}
        />
      </label>
      <label className="akw-field">
        <span className="akw-field__label">{tr('page.form.box')}</span>
        <select className="akw-input" value={form.box} onChange={(event) => setForm({ ...form, box: event.target.value })}>
          <option value="">{tr('page.form.box.ph')}</option>
          {state.boxes.filter((box) => !box.disabled).map((box) => (
            <option key={box.name} value={box.name}>{box.name}</option>
          ))}
        </select>
      </label>
      <label className="akw-field">
        <span className="akw-field__label">{tr('page.form.note')}</span>
        <input
          className="akw-input"
          value={form.note}
          placeholder={tr('page.form.note.ph')}
          onChange={(event) => setForm({ ...form, note: event.target.value })}
        />
      </label>
      <div className="akw-form__actions">
        <button type="button" className="akw-btn akw-btn--ghost" onClick={() => setForm(ACCOUNT_EMPTY)}>
          {tr('page.form.cancel')}
        </button>
        <button type="submit" className="akw-btn akw-btn--primary" disabled={!canSubmit}>
          {state.submitting ? tr('page.form.submitting') : tr('page.form.submit')}
        </button>
      </div>
    </form>
  )
}

/* ────────────────────────────── 站点管理 ────────────────────────────── */

function SitesTab({ tr }: { tr: (key: string, vars?: Record<string, string | number>) => string }) {
  const state = useStore()
  const [form, setForm] = useState(SITE_EMPTY)
  const canSubmit = form.baseUrl.trim() !== '' && !state.submitting

  async function submit(event: React.FormEvent): Promise<void> {
    event.preventDefault()
    if (!canSubmit) return
    const ok = await createSite({
      name: form.name.trim() || undefined,
      baseUrl: form.baseUrl.trim(),
      note: form.note.trim() || undefined,
    })
    if (ok) setForm(SITE_EMPTY)
  }

  return (
    <>
      <form className="akw-form" onSubmit={(event) => void submit(event)}>
        <div className="akw-form__head">
          <h2>{tr('site.title')}</h2>
          <span>{tr('site.desc')}</span>
        </div>
        <label className="akw-field">
          <span className="akw-field__label">{tr('site.form.name')}</span>
          <input
            className="akw-input"
            value={form.name}
            placeholder={tr('site.form.name.ph')}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
          />
        </label>
        <label className="akw-field">
          <span className="akw-field__label">{tr('site.form.url')}</span>
          <input
            className="akw-input"
            value={form.baseUrl}
            placeholder={tr('site.form.url.ph')}
            onChange={(event) => setForm({ ...form, baseUrl: event.target.value })}
          />
        </label>
        <label className="akw-field akw-field--wide">
          <span className="akw-field__label">{tr('site.form.note')}</span>
          <input
            className="akw-input"
            value={form.note}
            onChange={(event) => setForm({ ...form, note: event.target.value })}
          />
        </label>
        <div className="akw-form__actions">
          <button type="submit" className="akw-btn akw-btn--primary" disabled={!canSubmit}>
            {state.submitting ? tr('page.form.submitting') : tr('site.form.submit')}
          </button>
        </div>
      </form>

      {state.sites.length === 0 ? (
        <div className="akw-empty">
          <span className="akw-empty__title">{tr('site.empty.title')}</span>
          <span className="akw-empty__desc">{tr('site.empty.desc')}</span>
        </div>
      ) : (
        <ul className="akw-rows">
          {state.sites.map((site) => (
            <li key={site.id} className="akw-row">
              <div className="akw-row__main">
                <b title={site.name}>{site.name || site.host}</b>
                <span title={site.baseUrl}>{site.host || site.baseUrl}{site.note !== '' ? ` · ${site.note}` : ''}</span>
              </div>
              <span className="akw-row__meta">{tr('site.item.accounts', { count: site.accountCount })}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

/* ────────────────────────────── 盒子管理 ────────────────────────────── */

function BoxesTab({ tr }: { tr: (key: string, vars?: Record<string, string | number>) => string }) {
  const state = useStore()
  const [renaming, setRenaming] = useState<{ name: string; value: string } | null>(null)
  const [binding, setBinding] = useState<{ name: string; siteId: string } | null>(null)

  if (state.boxes.length === 0) {
    return (
      <div className="akw-empty">
        <span className="akw-empty__title">{tr('box.empty.title')}</span>
        <span className="akw-empty__desc">{tr('box.empty.desc')}</span>
      </div>
    )
  }

  return (
    <ul className="akw-rows">
      {state.boxes.map((box) => (
        <li key={box.name} className="akw-row akw-row--box">
          <div className="akw-row__main">
            <b title={box.name}>
              {box.name}
              {box.isDefault ? <span className="akw-chiplet">{tr('box.item.default')}</span> : null}
              {box.disabled ? <span className="akw-chiplet akw-chiplet--off">{tr('box.item.disabled')}</span> : null}
            </b>
            <span>
              {box.siteName ? tr('box.item.site', { site: box.siteName }) : ''}
            </span>
          </div>
          {renaming?.name === box.name ? (
            <span className="akw-row__inline">
              <input
                className="akw-input akw-input--inline"
                value={renaming.value}
                autoFocus
                placeholder={tr('box.form.newName')}
                onChange={(event) => setRenaming({ name: box.name, value: event.target.value })}
              />
              <button
                type="button"
                className="akw-btn akw-btn--sm"
                disabled={renaming.value.trim() === '' || state.busy === 'box-rename'}
                onClick={async () => {
                  const ok = await renameBox(box.name, renaming.value.trim())
                  if (ok) setRenaming(null)
                }}
              >
                {tr('box.action.rename')}
              </button>
              <button type="button" className="akw-btn akw-btn--sm akw-btn--ghost" onClick={() => setRenaming(null)}>
                {tr('page.action.cancel')}
              </button>
            </span>
          ) : binding?.name === box.name ? (
            <span className="akw-row__inline">
              <select
                className="akw-input akw-input--inline"
                value={binding.siteId}
                onChange={(event) => setBinding({ name: box.name, siteId: event.target.value })}
              >
                <option value="">{tr('page.form.site.ph')}</option>
                {state.sites.map((site) => (
                  <option key={site.id} value={site.id}>{site.name || site.baseUrl}</option>
                ))}
              </select>
              <button
                type="button"
                className="akw-btn akw-btn--sm"
                disabled={binding.siteId === '' || state.busy === `box-bind:${box.name}`}
                onClick={async () => {
                  await bindBoxSite(box.name, binding.siteId)
                  setBinding(null)
                }}
              >
                {tr('box.action.bind')}
              </button>
              <button type="button" className="akw-btn akw-btn--sm akw-btn--ghost" onClick={() => setBinding(null)}>
                {tr('page.action.cancel')}
              </button>
            </span>
          ) : (
            <span className="akw-row__inline">
              <span className="akw-row__meta">{tr('box.item.accounts', { count: box.count })}</span>
              <button
                type="button"
                className="akw-btn akw-btn--sm"
                onClick={() => setRenaming({ name: box.name, value: box.name })}
              >
                {tr('box.action.rename')}
              </button>
              <button
                type="button"
                className="akw-btn akw-btn--sm"
                disabled={state.sites.length === 0}
                onClick={() => setBinding({ name: box.name, siteId: box.siteId ?? '' })}
              >
                {tr('box.action.bind')}
              </button>
            </span>
          )}
        </li>
      ))}
    </ul>
  )
}

/* ────────────────────────────── 页面 ────────────────────────────── */

type Section = 'accounts' | 'tools'

const primaryNavStyle: React.CSSProperties = {
  display: 'flex',
  gap: 2,
  padding: '0 24px',
  borderBottom: '1px solid var(--akw-line, #0002)',
}

function primaryTabStyle(active: boolean): React.CSSProperties {
  return {
    appearance: 'none',
    border: 'none',
    background: 'transparent',
    color: 'inherit',
    padding: '10px 14px',
    fontSize: 14,
    fontWeight: active ? 650 : 500,
    opacity: active ? 1 : 0.55,
    borderBottom: active ? '2px solid var(--akw-brand, #366ef4)' : '2px solid transparent',
    cursor: 'pointer',
  }
}

export function AccountsPage(props: { t?: (key: string, vars?: Record<string, string | number>) => string } = {}) {
  const tr = typeof props.t === 'function' ? props.t : t
  const state = useStore()
  const [section, setSection] = useState<Section>('accounts')
  const [tab, setTab] = useState<Tab>('accounts')
  const [query, setQuery] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const now = useNow(30000)
  const loggedInRef = useRef(state.loggedIn)

  useEffect(() => {
    void refresh()
    // 自动同步：可见时每 60s 拉一次云端；窗口重新聚焦时也补一次
    const tick = () => {
      if (document.visibilityState !== 'visible') return
      if (!loggedInRef.current) {
        void refresh()
        return
      }
      void syncNow()
    }
    const timer = window.setInterval(tick, 60_000)
    const onVisible = () => {
      if (document.visibilityState === 'visible' && loggedInRef.current) void syncNow()
    }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    }
  }, [])

  const loggedIn = state.loggedIn

  useEffect(() => {
    loggedInRef.current = loggedIn
  }, [loggedIn])

  const tabs: Array<{ key: Tab; label: string; count?: number }> = [
    { key: 'accounts', label: tr('page.tab.accounts'), count: state.counts.accounts },
    { key: 'add', label: tr('page.tab.add') },
    { key: 'sites', label: tr('page.tab.sites'), count: state.counts.sites },
    { key: 'boxes', label: tr('page.tab.boxes'), count: state.counts.boxes },
  ]

  return (
    <div className="akw-page">
      <header className="akw-topbar">
        <div className="akw-topbar__left">
          <span className="akw-mark" aria-hidden="true">a</span>
          <div className="akw-brandtext">
            <h1>akso-workbench</h1>
            {loggedIn ? null : <p>账号中心未登录，配置工具仍可用</p>}
          </div>
        </div>
        <div className="akw-topbar__right">
          {loggedIn ? (
            <>
              <span
                className="akw-sync"
                title={state.syncedAt === null ? '' : new Date(state.syncedAt).toLocaleString('zh-CN')}
              >
                {state.syncedAt === null ? '尚未同步' : `同步于 ${fmtRelative(state.syncedAt, now)}`}
              </span>
              <div style={{ position: 'relative' }}>
                <button
                  type="button"
                  className="akw-btn"
                  aria-haspopup="menu"
                  aria-expanded={menuOpen}
                  onClick={() => setMenuOpen((value) => !value)}
                >
                  {state.profile?.displayName || state.profile?.email || '已登录'}
                  <span style={{ opacity: 0.5, fontSize: 10 }}>▾</span>
                </button>
                {menuOpen ? (
                  <>
                    <div style={{ position: 'fixed', inset: 0, zIndex: 40 }} onClick={() => setMenuOpen(false)} />
                    <div
                      role="menu"
                      style={{
                        position: 'absolute',
                        right: 0,
                        top: 'calc(100% + 6px)',
                        zIndex: 41,
                        minWidth: 176,
                        padding: 6,
                        borderRadius: 10,
                        border: '1px solid var(--dsw-alias-border-l3)',
                        background: 'var(--dsw-alias-bg-layer-1)',
                        boxShadow: '0 8px 24px rgba(0,0,0,.18)',
                      }}
                    >
                      <button
                        type="button"
                        role="menuitem"
                        style={menuItemStyle}
                        onClick={() => {
                          setMenuOpen(false)
                          const base = state.profile?.baseUrl
                          if (base) window.open(base, '_blank', 'noopener')
                        }}
                      >
                        打开 akso-cloud
                      </button>
                      <button
                        type="button"
                        role="menuitem"
                        style={menuItemStyle}
                        onClick={() => {
                          setMenuOpen(false)
                          void logout()
                        }}
                      >
                        退出登录
                      </button>
                    </div>
                  </>
                ) : null}
              </div>
            </>
          ) : (
            <span style={{ fontSize: 12, color: 'var(--dsw-alias-label-tertiary)' }}>未登录</span>
          )}
        </div>
      </header>

      {/* 一级导航：账号中心 / 配置工具 */}
      <nav style={primaryNavStyle} aria-label="模块导航">
        <button type="button" style={primaryTabStyle(section === 'accounts')} onClick={() => setSection('accounts')}>
          账号中心
        </button>
        <button type="button" style={primaryTabStyle(section === 'tools')} onClick={() => setSection('tools')}>
          配置工具
        </button>
      </nav>

      {section === 'accounts' ? (
        loggedIn ? (
          <>
            <nav className="akw-tabs" role="tablist" aria-label={tr('page.title')}>
              {tabs.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  role="tab"
                  aria-selected={tab === item.key}
                  className={`akw-tab${tab === item.key ? ' is-active' : ''}`}
                  onClick={() => setTab(item.key)}
                >
                  {item.label}
                  {item.count === undefined ? null : <i>{item.count}</i>}
                </button>
              ))}
            </nav>

            <div className="akw-body">
              {state.stale && state.syncError !== null ? (
                <div className="akw-notice akw-notice--err" role="alert">
                  <span className="akw-notice__grow">{tr('cloud.status.stale', { message: state.syncError })}</span>
                </div>
              ) : null}

              {state.flash !== null ? (
                <div className="akw-notice" role="status" aria-live="polite">
                  <span className="akw-notice__grow">{state.flash}</span>
                </div>
              ) : null}

              {state.status === 'loading' ? (
                <>
                  <div className="akw-skeleton" />
                  <div className="akw-skeleton" />
                </>
              ) : null}

              {state.status === 'error' ? (
                <div className="akw-notice akw-notice--err" role="alert">
                  <span className="akw-notice__grow">{tr('page.error', { message: state.error ?? '' })}</span>
                  <button type="button" className="akw-btn akw-btn--sm" onClick={() => void refresh()}>
                    {tr('page.retry')}
                  </button>
                </div>
              ) : null}

              {state.status === 'ready' && tab === 'accounts' ? <AccountsTab tr={tr} query={query} /> : null}
              {state.status === 'ready' && tab === 'add' ? <AddAccountTab tr={tr} /> : null}
              {state.status === 'ready' && tab === 'sites' ? <SitesTab tr={tr} /> : null}
              {state.status === 'ready' && tab === 'boxes' ? <BoxesTab tr={tr} /> : null}
            </div>
          </>
        ) : (
          <LoginGate tr={tr} embedded />
        )
      ) : (
        <div className="akw-body">
          <ConfigToolsTab />
        </div>
      )}
    </div>
  )
}