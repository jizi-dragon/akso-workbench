/**
 * 「配置工具」页（挂在 akso-workbench 页面里，不单独占左栏）。
 *
 * 结构：二级导航（认证 · 对象建模 · 布局 · 生命周期 · 工作流 · 菜单/通知）
 *   → 对象建模、布局再分栏
 *   → 右栏是这一区的接口卡片（点开看参数表 + 请求示例 + 返回 + 备注）。
 *
 * 目录与 `docs/reference/Akso平台内部API接口清单.md` 的 47 条一一对应；
 * 卡片底部的「已验证 / 过期」是**实测台账**：用户在测试中确认过输入输出的接口才标已验证（绿），
 * 其余一律标过期（红），见 capabilities.ts 的 APPROVED_TOOL_IDS。
 *
 * 定位仍是**台账**：只读能力目录（GET config/capabilities）+ 管 host 侧的 eGMP 会话，
 * 不写配置；写入走 plan/apply 工具，必须由人确认。
 */
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { api } from './api.ts'
import { APPROVED_TOOL_IDS } from '../shared/approved-tools.ts'

interface Param {
  name: string
  location: 'path' | 'body' | 'query'
  required?: boolean
  type?: string
  description?: string
}

interface Capability {
  id: string
  domain: string
  title: string
  description: string
  method: string
  path: string
  risk: 'low' | 'medium' | 'high'
  irreversible?: boolean
  evidence: 'verified' | 'partial'
  testStatus?: 'verified' | 'stale'
  params: Param[]
  example: unknown
  returns?: string
  notes?: string[]
}

interface Group { domain: string; label?: string; items: Capability[] }

interface Session {
  loggedIn: boolean
  baseUrl: string | null
  account: string | null
  savedAt: number | null
}

/** 账号条的一条：只有 hasPassword，没有口令字段 —— 口令从不进浏览器。 */
interface CredentialRow {
  id: string
  label: string
  account: string
  baseUrl: string
  siteName: string
  hasPassword: boolean
}

type SectionId = 'auth' | 'model' | 'layout' | 'lifecycle' | 'workflow' | 'menu'

interface Section {
  id: SectionId
  label: string
  hint: string
  domains: string[]
  /** 有分栏时，右栏只显示当前分栏的域。 */
  tabs?: Array<{ key: string; label: string }>
}

const SECTIONS: Section[] = [
  { id: 'auth', label: '认证', hint: 'eGMP OpenAPI 登录换 JWT。写入（生成计划 / 执行）都靠 host 侧这个会话。', domains: ['auth'] },
  {
    id: 'model',
    label: '对象建模',
    hint: '对象、字段、选项集的查询与写入 —— 建对象 → 建选项集 → 建字段。',
    domains: ['object', 'field', 'picklist'],
    tabs: [
      { key: 'object', label: '对象' },
      { key: 'field', label: '字段' },
      { key: 'picklist', label: '选项集' },
    ],
  },
  {
    id: 'layout',
    label: '布局',
    hint: '表单布局与列表布局：先读全量，改完再全量写回。',
    domains: ['layout', 'listlayout'],
    tabs: [
      { key: 'layout', label: '表单布局' },
      { key: 'listlayout', label: '列表布局' },
    ],
  },
  { id: 'lifecycle', label: '生命周期', hint: '对象状态机：状态节点、状态上的按钮动作。', domains: ['lifecycle'] },
  { id: 'workflow', label: '工作流', hint: '审批流：建流程 → 切编辑中 → 加节点 → 配按钮 → 发布。', domains: ['workflow'] },
  { id: 'menu', label: '菜单 / 通知', hint: '菜单树（母/子、排序）与按对象取通知模板。', domains: ['menu', 'notice'] },
]

const RISK_LABEL: Record<Capability['risk'], string> = { low: '低', medium: '中', high: '高' }

/** 卡片左下角的实测徽标：只有用户在测试里确认过的 id 才是「已验证」。 */
const isVerified = (item: Capability): boolean => APPROVED_TOOL_IDS.has(item.id)

/** 只读但走 POST 的接口，列表里要按"查询"展示。 */
const READ_IDS = new Set([
  'object.field.page',
  'object.fields.outfield',
  'workflow.page.list',
  'lifecycle.useraction.get',
  'menu.group.list',
  'notice.template.list',
])

type Kind = 'write' | 'query' | 'auth'

function kindOf(item: Capability): Kind {
  if (item.domain === 'auth') return 'auth'
  if (item.method === 'GET' || READ_IDS.has(item.id)) return 'query'
  return 'write'
}

const KIND_LABEL: Record<Kind, string> = {
  write: '写入',
  query: '查询',
  auth: '认证',
}

function EmptyState({ title, desc, note }: { title: string; desc: string; note?: string }) {
  return (
    <div className="akw-cfg__empty">
      <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth={1.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="7" y="7" width="34" height="34" rx="10" strokeDasharray="3 4" opacity=".5" />
        <path d="M17 20h14M17 26h9" opacity=".55" />
        <circle cx="32.5" cy="30.5" r="3.5" />
        <path d="M35 33l3 3" />
      </svg>
      <b>{title}</b>
      <span>{desc}</span>
      {note !== undefined ? <em>{note}</em> : null}
    </div>
  )
}

function ParamsTable({ params }: { params: Param[] }) {
  if (params.length === 0) return <div className="akw-cfg__noparam">无参数</div>
  return (
    <table className="akw-cfg__params">
      <thead>
        <tr>
          <th>参数</th>
          <th>位置</th>
          <th>必填</th>
          <th>说明</th>
        </tr>
      </thead>
      <tbody>
        {params.map((param) => (
          <tr key={param.name}>
            <td className="is-mono">{param.name}</td>
            <td>{param.location}</td>
            <td>{param.required === true ? '是' : '否'}</td>
            <td>{param.description === undefined || param.description === '' ? '—' : param.description}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ExampleBlock({ example }: { example: unknown }) {
  const [copied, setCopied] = useState(false)
  const text = JSON.stringify(example, null, 2) ?? '{}'

  async function copy(event: { stopPropagation: () => void }): Promise<void> {
    event.stopPropagation()
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="akw-cfg__ex">
      <div className="akw-cfg__exhead">
        <span>请求示例</span>
        <button type="button" className="akw-cfg__copy" onClick={(event) => void copy(event)}>
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre className="akw-cfg__code">{text}</pre>
    </div>
  )
}

function CapabilityCard({ item, open, onToggle }: { item: Capability; open: boolean; onToggle: () => void }) {
  const kind = kindOf(item)
  const verified = isVerified(item)
  return (
    <article
      className={`akw-cfg__card${item.irreversible === true ? ' is-blocked' : ''}${open ? ' is-open' : ''}`}
      role="button"
      tabIndex={0}
      aria-expanded={open}
      onClick={onToggle}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onToggle()
        }
      }}
    >
      <div className="akw-cfg__cardtop">
        <strong>{item.title}</strong>
        <div className="akw-cfg__flags">
          <span className={`akw-cfg__tag is-kind-${kind}`}>{KIND_LABEL[kind]}</span>
          {item.irreversible === true ? <span className="akw-cfg__tag is-danger">不可逆</span> : null}
        </div>
      </div>

      <code className="akw-cfg__id">{item.id}</code>
      <div className="akw-cfg__path">
        <b>{item.method}</b>
        <span>{item.path}</span>
      </div>
      <p className="akw-cfg__desc">{item.description}</p>

      <div className="akw-cfg__meta">
        <span className={`akw-cfg__tag ${verified ? 'is-ok' : 'is-danger'}`}>{verified ? '已验证' : '过期'}</span>
        <span className="akw-cfg__count">风险 {RISK_LABEL[item.risk]} · {item.params.length} 参数</span>
        <span className="akw-cfg__more">{open ? '收起' : '详情'}</span>
      </div>

      {open ? (
        <div className="akw-cfg__detail" onClick={(event) => event.stopPropagation()}>
          <ParamsTable params={item.params} />
          <ExampleBlock example={item.example} />
          {item.returns !== undefined ? <p className="akw-cfg__ret"><b>返回</b>{item.returns}</p> : null}
          {item.notes !== undefined && item.notes.length > 0 ? (
            <ul className="akw-cfg__notes">
              {item.notes.map((note) => <li key={note}>{note}</li>)}
            </ul>
          ) : null}
        </div>
      ) : null}
    </article>
  )
}

/**
 * eGMP 会话条 + 登录表单。
 *
 * 口令只往 host 发这一次（host 换 JWT 落盘，浏览器不保存任何凭据），
 * 登录成功后 host 的 `config/session` 才有内容；写入（plan/apply）靠它。
 */
function EgmpSessionPanel({ session, onChange }: { session: Session | null; onChange: (next: Session) => void }) {
  const [open, setOpen] = useState(false)
  const [baseUrl, setBaseUrl] = useState('')
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const loggedIn = session?.loggedIn === true

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const next = await api.configLogin({ baseUrl: baseUrl.trim(), account: account.trim(), password })
      onChange({ loggedIn: true, baseUrl: next.baseUrl, account: next.account, savedAt: next.savedAt })
      setPassword('')
      setOpen(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function logout(): Promise<void> {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await api.configLogout()
      onChange({ loggedIn: false, baseUrl: null, account: null, savedAt: null })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`akw-cfg__auth${loggedIn ? ' is-on' : ''}`}>
      <div className="akw-cfg__session">
        <span className="akw-cfg__dot" aria-hidden="true" />
        {session === null ? (
          <span className="akw-cfg__sessiontext">读取 eGMP 会话状态…</span>
        ) : loggedIn ? (
          <span className="akw-cfg__sessiontext">
            eGMP 已登录：<b>{session.account ?? '—'}</b> · {session.baseUrl ?? '—'}
            <em>令牌只在 host 侧，浏览器拿不到</em>
          </span>
        ) : (
          <span className="akw-cfg__sessiontext">
            eGMP 未登录 —— 查询类接口不受影响；<b>写入（生成计划 / 执行）需要先登录</b>。
          </span>
        )}
        <div className="akw-cfg__sessionacts">
          {loggedIn ? (
            <button type="button" className="akw-btn akw-btn--sm akw-btn--ghost" onClick={() => void logout()} disabled={busy}>
              退出登录
            </button>
          ) : (
            <button type="button" className="akw-btn akw-btn--sm" onClick={() => setOpen(!open)} aria-expanded={open}>
              {open ? '收起' : '手动登录'}
            </button>
          )}
        </div>
      </div>

      {!loggedIn ? <CredentialLogin onDone={onChange} /> : null}

      {open && !loggedIn ? (
        <form className="akw-form akw-form--single" onSubmit={(event) => void submit(event)}>
          <div className="akw-form__head">
            <h2>登录 eGMP</h2>
            <span>口令只用于换 JWT，不落盘；令牌存在 host 侧</span>
          </div>
          <label className="akw-field akw-field--wide">
            <span className="akw-field__label">环境地址</span>
            <input
              className="akw-input"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="https://<你的 eGMP 环境>"
              autoComplete="url"
              spellCheck={false}
              required
            />
          </label>
          <label className="akw-field">
            <span className="akw-field__label">账号</span>
            <input
              className="akw-input"
              value={account}
              onChange={(event) => setAccount(event.target.value)}
              autoComplete="username"
              spellCheck={false}
              required
            />
          </label>
          <label className="akw-field">
            <span className="akw-field__label">口令</span>
            <input
              className="akw-input"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          {error !== '' ? <p className="akw-cfg__hint is-error">{error}</p> : null}
          <div className="akw-form__actions">
            <button type="button" className="akw-btn akw-btn--sm akw-btn--ghost" onClick={() => setOpen(false)} disabled={busy}>
              取消
            </button>
            <button type="submit" className="akw-btn akw-btn--sm akw-btn--primary" disabled={busy}>
              {busy ? '登录中…' : '连接'}
            </button>
          </div>
        </form>
      ) : null}
    </div>
  )
}

/**
 * 账号库登录：口令存在 akso-cloud（Fernet 加密），host 按账号 id 取一次明文、
 * 立刻换成 JWT 后丢掉。页面拿到的只有 `hasPassword`，所以这里只显示 ····。
 */
function CredentialLogin({ onDone }: { onDone: (next: Session) => void }) {
  const [rows, setRows] = useState<CredentialRow[] | null>(null)
  const [current, setCurrent] = useState<string | null>(null)
  const [cloudLoggedIn, setCloudLoggedIn] = useState(true)
  const [busyId, setBusyId] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const payload = await api.configCredentials()
        if (!alive) return
        setRows(payload.accounts)
        setCurrent(payload.current)
        setCloudLoggedIn(payload.cloudLoggedIn)
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => { alive = false }
  }, [])

  async function useAccount(row: CredentialRow): Promise<void> {
    if (busyId !== '') return
    setBusyId(row.id)
    setError('')
    try {
      const result = await api.configLoginWithAccount(row.id)
      onDone({
        loggedIn: true,
        baseUrl: result.session.baseUrl,
        account: result.session.account,
        savedAt: result.session.savedAt,
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId('')
    }
  }

  return (
    <div className="akw-cfg__creds">
      <div className="akw-cfg__credshead">
        <b>用账号库口令登录</b>
        <span>口令存在 akso-cloud，host 取到后立刻换 JWT —— 浏览器只显示 ····，拿不到明文</span>
      </div>
      {!cloudLoggedIn ? (
        <p className="akw-cfg__hint">akso-cloud 未登录：先在账号页登录，账号条里才有口令可用。</p>
      ) : rows === null ? (
        <p className="akw-cfg__hint">读取账号库…</p>
      ) : rows.length === 0 ? (
        <p className="akw-cfg__hint">账号库里还没有账号。</p>
      ) : (
        <ul className="akw-cfg__credlist">
          {rows.map((row) => (
            <li key={row.id} className={`akw-cfg__credrow${row.id === current ? ' is-current' : ''}`}>
              <div className="akw-cfg__credtext">
                <b>{row.label}</b>
                <span className="akw-cfg__credmeta">
                  {row.account} · {row.siteName}
                  {row.id === current ? ' · 当前连接' : ''}
                </span>
                <span className="akw-cfg__credmask" aria-label="口令已隐藏">········</span>
              </div>
              <button
                type="button"
                className="akw-btn akw-btn--sm akw-btn--primary"
                onClick={() => void useAccount(row)}
                disabled={!row.hasPassword || busyId !== ''}
                title={row.hasPassword ? `用账号库里存的口令登录 ${row.baseUrl}` : '这个账号在 akso-cloud 里没有存口令'}
              >
                {busyId === row.id ? '登录中…' : row.hasPassword ? '登录' : '无口令'}
              </button>
            </li>
          ))}
        </ul>
      )}
      {error !== '' ? <p className="akw-cfg__hint is-error">{error}</p> : null}
    </div>
  )
}

export function ConfigToolsTab() {
  const [groups, setGroups] = useState<Group[]>([])
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [sectionId, setSectionId] = useState<SectionId>('auth')
  const [subTab, setSubTab] = useState('')
  const [openId, setOpenId] = useState('')

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const payload = await api.configCapabilities()
        if (!alive) return
        setGroups((payload.groups ?? []) as unknown as Group[])
        setSession((payload.session ?? null) as Session | null)
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => { alive = false }
  }, [])

  const byDomain = useMemo(() => {
    const map = new Map<string, Capability[]>()
    for (const group of groups) map.set(group.domain, group.items)
    return map
  }, [groups])

  const section = SECTIONS.find((item) => item.id === sectionId) ?? SECTIONS[0]!
  const sectionItems = useMemo(
    () => section.domains.flatMap((domain) => byDomain.get(domain) ?? []),
    [section, byDomain],
  )
  const activeTab = section.tabs?.some((tab) => tab.key === subTab) === true
    ? subTab
    : (section.tabs?.[0]?.key ?? '')
  const visible = section.tabs === undefined ? sectionItems : (byDomain.get(activeTab) ?? [])

  const stats = useMemo(() => ({
    total: sectionItems.length,
    verified: sectionItems.filter((item) => isVerified(item)).length,
    stale: sectionItems.filter((item) => !isVerified(item)).length,
    irreversible: sectionItems.filter((item) => item.irreversible === true).length,
    params: sectionItems.reduce((sum, item) => sum + item.params.length, 0),
    writes: sectionItems.filter((item) => kindOf(item) === 'write').length,
  }), [sectionItems])

  function pick(next: SectionId): void {
    setSectionId(next)
    setSubTab('')
    setOpenId('')
  }

  return (
    <div className="akw-cfg">
      <nav className="akw-cfg__tabs" aria-label="配置工具分区">
        {SECTIONS.map((item) => {
          const count = item.domains.flatMap((domain) => byDomain.get(domain) ?? []).length
          const active = item.id === section.id
          return (
            <button
              key={item.id}
              type="button"
              className={`akw-cfg__tab${active ? ' is-active' : ''}`}
              aria-current={active ? 'page' : undefined}
              onClick={() => pick(item.id)}
            >
              {item.label}
              <i>{count}</i>
            </button>
          )
        })}
      </nav>

      <div className="akw-cfg__main">
        <div className="akw-cfg__lede">
          <p>{section.hint}</p>
          <div className="akw-cfg__stats">
            <span className="akw-cfg__stat"><b>{stats.total}</b><em>接口</em></span>
            {stats.writes > 0 ? <span className="akw-cfg__stat"><b>{stats.writes}</b><em>写入</em></span> : null}
            {stats.total === 0 ? null : <span className="akw-cfg__stat"><b>{stats.params}</b><em>参数</em></span>}
            {stats.verified > 0 ? <span className="akw-cfg__stat is-ok"><b>{stats.verified}</b><em>已验证</em></span> : null}
            {stats.stale > 0 ? <span className="akw-cfg__stat is-danger"><b>{stats.stale}</b><em>过期</em></span> : null}
            {stats.irreversible > 0 ? <span className="akw-cfg__stat is-danger"><b>{stats.irreversible}</b><em>不可逆</em></span> : null}
          </div>
        </div>

        {stats.writes > 0 ? (
          <p className="akw-cfg__legend">
            写入即生效，平台侧没有回滚；<b className="is-ok">已验证</b> = 你在测试中确认过输入与输出的接口，
            <b className="is-danger">过期</b> = 还没验证过，调用前先核对参数。上生产前先在测试对象上跑一遍。
            示例是参数对象：path / query 参数会拼进 URL，其余整体作为请求体。
          </p>
        ) : null}

        {section.id === 'auth' ? (
          <EgmpSessionPanel session={session} onChange={setSession} />
        ) : null}

        {section.tabs !== undefined ? (
          <div className="akw-cfg__subtabs" role="group" aria-label={`${section.label}分类`}>
            {section.tabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                aria-pressed={activeTab === tab.key}
                className={`akw-cfg__subtab${activeTab === tab.key ? ' is-active' : ''}`}
                onClick={() => { setSubTab(tab.key); setOpenId('') }}
              >
                {tab.label}
                <span>{(byDomain.get(tab.key) ?? []).length}</span>
              </button>
            ))}
          </div>
        ) : null}

        {loading ? <div className="akw-cfg__hint">正在读取能力目录…</div> : null}
        {error !== '' ? <div className="akw-cfg__hint is-error">能力目录读取失败：{error}</div> : null}

        {!loading && visible.length > 0 ? (
          <div className="akw-cfg__grid">
            {visible.map((item) => (
              <CapabilityCard
                key={item.id}
                item={item}
                open={openId === item.id}
                onToggle={() => setOpenId(openId === item.id ? '' : item.id)}
              />
            ))}
          </div>
        ) : null}

        {!loading && visible.length === 0 && error === '' ? (
          <EmptyState
            title="这一栏还没有接口"
            desc="能力目录里没有登记这个域的接口。"
            note="检查 plugin/src/config/capabilities.ts 是否漏了域映射。"
          />
        ) : null}
      </div>
    </div>
  )
}
