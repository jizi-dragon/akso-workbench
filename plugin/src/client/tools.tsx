/**
 * 「配置工具」页（挂在 akso-workbench 页面里，不单独占左栏）。
 *
 * 结构：二级导航（认证/查询 · 对象处理 · 菜单 · 生命周期处理 · 工作流处理）
 *   → 对象处理再按「对象 / 选项集 / 字段 / 表单布局 / 列表布局」分栏
 *   → 右栏是这一区的接口卡片（点开看参数表）。
 *
 * 定位仍是**台账**，不是执行台：只读能力目录（GET config/capabilities），
 * 不登录 eGMP、不写配置；写入走 plan/apply 工具，必须由人确认。
 * 生命周期与工作流两区先占位，流程设计好再往里放工具。
 */
import { useEffect, useMemo, useState } from 'react'

const BASE = 'api/akso-workbench/config'

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
  params: Param[]
}

interface Group { domain: string; items: Capability[] }

interface Session {
  loggedIn: boolean
  baseUrl: string | null
  account: string | null
  savedAt: number | null
}

type SectionId = 'access' | 'objects' | 'menu' | 'lifecycle' | 'workflow'

interface Section {
  id: SectionId
  label: string
  hint: string
  domains: string[]
}

/** 分区导航：标签用用户的话，`domains` 对应能力目录里的域。 */
const SECTIONS: Section[] = [
  { id: 'access', label: '认证 / 查询', hint: 'eGMP OpenAPI 登录换 JWT，以及只读查询：对象、字段、选项集、生命周期状态。', domains: ['auth', 'query'] },
  { id: 'objects', label: '对象处理', hint: '对象、选项集、字段、表单布局、列表布局的写入能力。', domains: ['object', 'picklist', 'field', 'layout', 'listlayout'] },
  { id: 'menu', label: '菜单', hint: '菜单树查询、新建（母/子）与整棵树的排序替换。', domains: ['menu'] },
  { id: 'lifecycle', label: '生命周期处理', hint: '对象状态机：状态、流转规则。', domains: ['lifecycle'] },
  { id: 'workflow', label: '工作流处理', hint: '审批流与流程编排。', domains: [] },
]
/** 对象处理下面的三级分栏。 */
const OBJECT_TABS: Array<{ key: string; label: string }> = [
  { key: 'object', label: '对象' },
  { key: 'picklist', label: '选项集' },
  { key: 'field', label: '字段' },
  { key: 'layout', label: '表单布局' },
  { key: 'listlayout', label: '列表布局' },
]

/** 台账状态：只影响展示，不改变引擎行为（引擎对受阻项在 plan 阶段就拒绝）。 */
const STATUS: Record<string, { label: '受阻' | '未实测'; note: string }> = {
  'layout.save': { label: '受阻', note: '平台要求 section.belongId 由 FullPath 生成，规则待录制' },
  'menu.submit': { label: '未实测', note: '平台没有按 code 预检菜单的接口，尚未在测试环境验证' },
  'menu.sort': { label: '未实测', note: '全量替换菜单树，尚未在测试环境验证' },
  'lifecycle.status.create': { label: '未实测', note: '需要对象先开启生命周期（不可逆），尚未验证' },
  'lifecycle.status.update': { label: '未实测', note: '需要对象先开启生命周期（不可逆），尚未验证' },
}

/**
 * 写入 / 查询。
 *
 * 这里不评"风险等级"：**所有写入操作都不可逆**（提交即生效，平台侧没有撤销），
 * 查询类只读、随便点。认证是换 JWT，既不写配置也不读数据，单列一类。
 */
type Kind = 'write' | 'query' | 'auth'

function kindOf(item: Capability): Kind {
  if (item.domain === 'query') return 'query'
  if (item.domain === 'auth') return 'auth'
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
        {params.map((p) => (
          <tr key={p.name}>
            <td className="is-mono">{p.name}</td>
            <td>{p.location}</td>
            <td>{p.required === true ? '是' : '否'}</td>
            <td>{p.description ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function CapabilityCard({ item, open, onToggle }: { item: Capability; open: boolean; onToggle: () => void }) {
  const status = STATUS[item.id]
  const kind = kindOf(item)
  return (
    <article
      className={`akw-cfg__card${status?.label === '受阻' ? ' is-blocked' : ''}${open ? ' is-open' : ''}`}
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
        </div>
      </div>

      <code className="akw-cfg__id">{item.id}</code>
      <div className="akw-cfg__path">
        <b>{item.method}</b>
        <span>{item.path}</span>
      </div>
      <p className="akw-cfg__desc">{item.description}</p>

      <div className="akw-cfg__meta">
        {status === undefined
          ? <span className="akw-cfg__tag is-ok">已实现</span>
          : <span className={`akw-cfg__tag ${status.label === '受阻' ? 'is-danger' : 'is-warn'}`}>{status.label}</span>}
        <span className="akw-cfg__count">{item.params.length} 个参数</span>
        <span className="akw-cfg__more">{open ? '收起' : '详情'}</span>
      </div>

      {open ? (
        <div className="akw-cfg__detail" onClick={(event) => event.stopPropagation()}>
          {status !== undefined ? <p className="akw-cfg__note">{status.note}</p> : null}
          <ParamsTable params={item.params} />
        </div>
      ) : null}
    </article>
  )
}

export function ConfigToolsTab() {
  const [groups, setGroups] = useState<Group[]>([])
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [sectionId, setSectionId] = useState<SectionId>('access')
  const [objectTab, setObjectTab] = useState('object')
  const [openId, setOpenId] = useState('')

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const response = await fetch(new URL(`${BASE}/capabilities`, document.baseURI).toString(), { cache: 'no-store' })
        const payload = (await response.json()) as { groups?: Group[]; session?: Session }
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        if (!alive) return
        setGroups(payload.groups ?? [])
        setSession(payload.session ?? null)
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

  const section = SECTIONS.find((s) => s.id === sectionId) ?? SECTIONS[0]!
  /** 生命周期 / 工作流：流程还没做，只留占位，不列已登记的底层接口。 */
  const placeholder = section.id === 'lifecycle' || section.id === 'workflow'
  const sectionItems = useMemo(
    () => section.domains.flatMap((domain) => byDomain.get(domain) ?? []),
    [section, byDomain],
  )
  const visible = useMemo(() => {
    if (placeholder) return []
    if (section.id !== 'objects') return sectionItems
    return byDomain.get(objectTab) ?? []
  }, [placeholder, section.id, objectTab, sectionItems, byDomain])

  const stats = useMemo(() => ({
    total: sectionItems.length,
    blocked: sectionItems.filter((item) => STATUS[item.id]?.label === '受阻').length,
    untested: sectionItems.filter((item) => STATUS[item.id]?.label === '未实测').length,
    params: sectionItems.reduce((sum, item) => sum + item.params.length, 0),
    writes: sectionItems.filter((item) => kindOf(item) === 'write').length,
  }), [sectionItems])

  function pick(next: SectionId): void {
    setSectionId(next)
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
            {placeholder ? null : (
              <>
                <span className="akw-cfg__stat"><b>{stats.total}</b><em>接口</em></span>
                {stats.total === 0 ? null : <span className="akw-cfg__stat"><b>{stats.params}</b><em>参数</em></span>}
                {stats.blocked > 0 ? <span className="akw-cfg__stat is-danger"><b>{stats.blocked}</b><em>受阻</em></span> : null}
                {stats.untested > 0 ? <span className="akw-cfg__stat is-warn"><b>{stats.untested}</b><em>未实测</em></span> : null}
              </>
            )}
          </div>
        </div>

        {!placeholder && stats.writes > 0 ? (
          <p className="akw-cfg__legend">写入类操作都不可撤销（提交即生效，平台侧没有回滚）；查询类只读。</p>
        ) : null}

        {section.id === 'access' ? (
          <div className={`akw-cfg__session${session?.loggedIn === true ? ' is-on' : ''}`}>
            <span className="akw-cfg__dot" aria-hidden="true" />
            {session === null
              ? <span>读取 eGMP 会话状态…</span>
              : session.loggedIn
                ? <span>eGMP 已登录：<b>{session.account ?? '—'}</b> · {session.baseUrl ?? '—'}</span>
                : <span>eGMP 未登录 —— 查询能用的部分不依赖它；写入流程需要先登录（登录入口待接入）</span>}
          </div>
        ) : null}

        {section.id === 'objects' ? (
          <div className="akw-cfg__subtabs" role="group" aria-label="对象处理分类">
            {OBJECT_TABS.map((tab) => (
              <button
                key={tab.key}
                type="button"
                aria-pressed={objectTab === tab.key}
                className={`akw-cfg__subtab${objectTab === tab.key ? ' is-active' : ''}`}
                onClick={() => { setObjectTab(tab.key); setOpenId('') }}
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

        {!loading && visible.length === 0 && section.id === 'lifecycle' ? (
          <EmptyState
            title="生命周期处理还没做"
            desc="对象状态机的写入流程还没设计，这一区先留空。"
            note={sectionItems.length > 0 ? `底层已登记 ${sectionItems.length} 个状态写入接口（未实测），等流程定下来再接进来。` : undefined}
          />
        ) : null}

        {!loading && visible.length === 0 && section.id === 'workflow' ? (
          <EmptyState
            title="工作流处理还没做"
            desc="审批流与流程编排还没有接口封装，这一区先留空。"
            note="等流程与接口确认后，再把能力登记进 config/capabilities。"
          />
        ) : null}

        {!loading && visible.length === 0 && section.id !== 'lifecycle' && section.id !== 'workflow' ? (
          <div className="akw-cfg__hint">这一区还没有登记接口。</div>
        ) : null}
      </div>
    </div>
  )
}
