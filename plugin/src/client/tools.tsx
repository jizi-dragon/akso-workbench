/**
 * 「配置工具」页（挂在 akso-workbench 页面里，不单独占左栏）。
 *
 * 结构：二级导航（对象建模 · 布局 · 生命周期 · 工作流 · 菜单/通知）
 *   → 对象建模、布局再分栏
 *   → 右栏是这一区的接口卡片；**点卡片弹窗看详情**（不在卡片里原地展开，
 *     否则一张卡片的参数表会把同排其它卡片挤变形）。
 *
 * 文案来源：`docs/knowledge/基础API使用指南.md`。
 *   host 每次下发能力目录时现读这份文档（按 mtime 缓存），所以改完文档**刷新页面**就生效，
 *   不用重新构建插件。指南只覆盖「介绍 / 返回 / 要点 / 案例」；method、path、参数表、
 *   风险这些结构字段仍以 capabilities.ts 为准。文档里没写的接口回落到目录里的旧文案。
 *
 * 目录的结构字段与 `docs/reference/Akso平台内部API接口清单.md`、实测抓包对齐；
 * 卡片底部的「已验证 / 过期」是**实测台账**：用户在测试中确认过输入输出的接口才标已验证（绿），
 * 其余一律标过期（红），见 capabilities.ts 的 APPROVED_TOOL_IDS。
 *
 * 定位仍是**台账**：只读能力目录（GET config/capabilities），不写配置；
 * 写入走 plan/apply 工具，必须由人确认（审批窗弹在对话里；页面上的开关只决定要不要弹）。
 *
 * 这里**不显示登录态**：eGMP 令牌由底部账号条自动取（akso-cloud 里的账号口令）、
 * 过期自动续，本页只负责把接口讲清楚。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api.ts'
import { APPROVED_TOOL_IDS } from '../shared/approved-tools.ts'

interface Param {
  name: string
  location: 'path' | 'body' | 'query'
  required?: boolean
  type?: string
  description?: string
}

/** 文档里的一个调用案例（`#### 案例 …` 一节）。 */
interface ApiCase {
  title: string
  note?: string
  params: unknown
  expected?: string
}

/** 标准配置方案里的一步：开头是接口 id，后面是做什么。 */
interface RecipeStep {
  api?: string
  text: string
}

/** 「标准配置使用规范.md」里的一个方案。 */
interface Recipe {
  capabilityId: string
  title: string
  summary?: string
  steps: RecipeStep[]
  checks: string[]
  payload?: unknown
  expected?: string
}

interface ActionGroup {
  name: string
  scope: string[]
  rows: Array<{ value: string; label: string; note?: string }>
}

interface RecipeGuideInfo {
  path: string | null
  updatedAt: string | null
  warnings: string[]
  coverage: { total: number; documented: number; recipes: number }
}

interface ActionMapInfo {
  path: string | null
  updatedAt: string | null
  warnings: string[]
  groups: ActionGroup[]
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
  readonly?: boolean
  evidence: 'verified' | 'partial'
  testStatus?: 'verified' | 'stale'
  params: Param[]
  example: unknown
  returns?: string
  notes?: string[]
  /** 指南里的一条案例；没写时 host 回落到目录里的 example。 */
  case?: ApiCase
  /** 指南登记了这一条。 */
  documented?: boolean
  /** 标准配置方案（「标准配置使用规范.md」）；只有部分接口有。 */
  recipes?: Recipe[]
}

interface GuideInfo {
  path: string | null
  updatedAt: string | null
  warnings: string[]
  coverage: { total: number; documented: number }
}

interface Group { domain: string; label?: string; items: Capability[] }

type SectionId = 'model' | 'layout' | 'lifecycle' | 'workflow' | 'menu'

interface Section {
  id: SectionId
  label: string
  hint: string
  domains: string[]
  /** 有分栏时，右栏只显示当前分栏的域。 */
  tabs?: Array<{ key: string; label: string }>
}

const SECTIONS: Section[] = [
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

/** 只读但走 POST 的接口在目录里标了 readonly，列表里按"查询"展示。 */
type Kind = 'write' | 'query'

function kindOf(item: Capability): Kind {
  if (item.method === 'GET' || item.readonly === true) return 'query'
  return 'write'
}

const KIND_LABEL: Record<Kind, string> = {
  write: '写入',
  query: '查询',
}

const caseOf = (item: Capability): ApiCase => item.case ?? { title: '基础示例', params: item.example }

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
    <table className="akw-api__params">
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

function CodeBlock({ value, label = '请求参数' }: { value: unknown; label?: string }) {
  const [copied, setCopied] = useState(false)
  const text = JSON.stringify(value, null, 2) ?? '{}'

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="akw-api__code">
      <div className="akw-api__codehead">
        <span>{label}</span>
        <button type="button" className="akw-api__copy" onClick={() => void copy()}>
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre>{text}</pre>
    </div>
  )
}

/** 一条案例：说明 + 参数代码块 + 预期，直接展开在折叠体里，不再套一层。 */
function CaseBlock({ item }: { item: ApiCase }) {
  return (
    <div className="akw-api__caseBody is-flat">
      <p className="akw-api__caseTitle">{item.title}</p>
      {item.note !== undefined ? <p className="akw-api__caseNote">{item.note}</p> : null}
      <CodeBlock value={item.params} />
      {item.expected !== undefined ? (
        <p className="akw-api__expect"><b>预期</b>{item.expected}</p>
      ) : null}
    </div>
  )
}

/** 标准配置方案：一条方案一层折叠，步骤 / 校验 / 示例都在折叠体里。 */
function RecipeList({ recipes }: { recipes: Recipe[] }) {
  return (
    <div className="akw-api__recipes">
      {recipes.map((recipe, index) => (
        <details key={`${recipe.title}-${index}`} className="akw-api__recipe" open={index === 0}>
          <summary>
            <span className="akw-api__caseTitle">{recipe.title}</span>
            <span className="akw-api__caseNo">{recipe.steps.length} 步</span>
          </summary>
          <div className="akw-api__caseBody">
            {recipe.summary !== undefined ? <p className="akw-api__caseNote">{recipe.summary}</p> : null}
            {recipe.steps.length > 0 ? (
              <ol className="akw-api__steps">
                {recipe.steps.map((step, stepIndex) => (
                  <li key={`${step.text}-${stepIndex}`}>
                    {step.api !== undefined ? <code className="akw-api__stepApi">{step.api}</code> : null}
                    <span>{step.text}</span>
                  </li>
                ))}
              </ol>
            ) : null}
            {recipe.checks.length > 0 ? (
              <div className="akw-api__checks">
                <b>校验</b>
                <ul>
                  {recipe.checks.map((check) => <li key={check}>{check}</li>)}
                </ul>
              </div>
            ) : null}
            {recipe.payload !== undefined ? <CodeBlock value={recipe.payload} label="示例请求" /> : null}
            {recipe.expected !== undefined ? (
              <p className="akw-api__expect"><b>预期</b>{recipe.expected}</p>
            ) : null}
          </div>
        </details>
      ))}
    </div>
  )
}

/** 动作类型映射：按作用域分组的小表。 */
function ActionMapTable({ groups }: { groups: ActionGroup[] }) {
  return (
    <div className="akw-api__actions">
      {groups.map((group) => (
        <div key={group.name} className="akw-api__actionGroup">
          <div className="akw-api__actionHead">
            <b>{group.name}</b>
            {group.scope.length > 0 ? <em>{group.scope.join(' / ')}</em> : null}
          </div>
          <table className="akw-api__params">
            <thead>
              <tr>
                <th>值</th>
                <th>含义</th>
                <th>备注</th>
              </tr>
            </thead>
            <tbody>
              {group.rows.length === 0 ? (
                <tr>
                  <td colSpan={3} className="akw-api__actionEmpty">
                    待补：这组映射还没登记值，先在「动作类型映射.md」里补表再刷新。
                  </td>
                </tr>
              ) : (
                group.rows.map((row) => (
                  <tr key={`${group.name}-${row.value}`}>
                    <td className="is-mono">{row.value}</td>
                    <td>{row.label}</td>
                    <td>{row.note ?? '—'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}

function CapabilityDialog({ item, guide, recipesGuide, actionMap, onClose }: {
  item: Capability
  guide: GuideInfo | null
  recipesGuide: RecipeGuideInfo | null
  actionMap: ActionMapInfo | null
  onClose: () => void
}) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  const kind = kindOf(item)
  const verified = isVerified(item)
  const oneCase = caseOf(item)
  const recipes = item.recipes ?? []
  // 只显示与当前接口域相关的几组动作映射。
  const actionGroups = (actionMap?.groups ?? []).filter((group) =>
    group.scope.length === 0 || group.scope.includes('通用') || group.scope.includes(item.domain),
  )

  useEffect(() => {
    const restore = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeRef.current?.focus()
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onCloseRef.current()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      restore?.focus()
    }
  }, [])

  return (
    <div
      className="akw-api"
      role="presentation"
      onClick={(event) => {
        // 只认点在遮罩本身上：在窗内按下、拖到遮罩上松手，不该关窗。
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        className="akw-api__card"
        role="dialog"
        aria-modal="true"
        aria-label={`${item.title} 接口详情`}
      >
        <header className="akw-api__head">
          <div className="akw-api__headMain">
            <b>{item.title}</b>
            <code className="akw-api__id">{item.id}</code>
          </div>
          <button ref={closeRef} type="button" className="akw-api__close" aria-label="关闭" onClick={onClose}>
            ✕
          </button>
        </header>

        <div className="akw-api__endpoint">
          <b className={`is-kind-${kind}`}>{item.method}</b>
          <span>{item.path}</span>
        </div>

        <div className="akw-api__flags">
          <span className={`akw-cfg__tag ${verified ? 'is-ok' : 'is-danger'}`}>{verified ? '已验证' : '过期'}</span>
          <span className="akw-cfg__tag">风险 {RISK_LABEL[item.risk]}</span>
          {item.irreversible === true ? <span className="akw-cfg__tag is-danger">不可逆</span> : null}
          {item.documented === false ? <span className="akw-cfg__tag">文案回落到目录</span> : null}
        </div>

        <div className="akw-api__body">
          <p className="akw-api__intro">{item.description}</p>

          <details className="akw-api__fold" open>
            <summary>
              <span>参数介绍</span>
              <i>{item.params.length}</i>
            </summary>
            <div className="akw-api__foldBody">
              <ParamsTable params={item.params} />
            </div>
          </details>

          <details className="akw-api__fold">
            <summary>
              <span>案例介绍</span>
              <i>1</i>
            </summary>
            <div className="akw-api__foldBody">
              <CaseBlock item={oneCase} />
            </div>
          </details>

          {recipes.length > 0 ? (
            <details className="akw-api__fold">
              <summary>
                <span>标准配置用法</span>
                <i>{recipes.length}</i>
              </summary>
              <div className="akw-api__foldBody">
                <RecipeList recipes={recipes} />
                {recipesGuide !== null && recipesGuide.path !== null ? (
                  <p className="akw-api__source">
                    方案来自 <code>{recipesGuide.path}</code>
                    {recipesGuide.updatedAt === null ? null : `（${formatTime(recipesGuide.updatedAt)} 更新）`}
                  </p>
                ) : null}
              </div>
            </details>
          ) : null}

          <details className="akw-api__fold">
            <summary>
              <span>输出结果</span>
              <i>{item.returns !== undefined ? '有' : '—'}</i>
            </summary>
            <div className="akw-api__foldBody">
              <p className="akw-api__ret">
                {item.returns !== undefined ? item.returns : '目录里没有记录返回结构；以实际调用结果为准。'}
              </p>
              {item.notes !== undefined && item.notes.length > 0 ? (
                <ul className="akw-api__notes">
                  {item.notes.map((note) => <li key={note}>{note}</li>)}
                </ul>
              ) : null}
            </div>
          </details>
        </div>

        <footer className="akw-api__foot">
          <span>
            {guide === null || guide.path === null
              ? '文案来自接口目录'
              : `文案来自 ${guide.path}${guide.updatedAt === null ? '' : ` · ${formatTime(guide.updatedAt)} 更新`}`
            }
          </span>
          <span className="akw-api__footHint">改文档后刷新页面即可生效，无需重新构建。</span>
        </footer>
      </div>
    </div>
  )
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function CapabilityCard({ item, onOpen }: { item: Capability; onOpen: () => void }) {
  const kind = kindOf(item)
  const verified = isVerified(item)
  const recipeCount = item.recipes?.length ?? 0
  return (
    <article
      className={`akw-cfg__card${item.irreversible === true ? ' is-blocked' : ''}`}
      role="button"
      tabIndex={0}
      aria-haspopup="dialog"
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onOpen()
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
        <span className="akw-cfg__count">风险 {RISK_LABEL[item.risk]} · {item.params.length} 参数{recipeCount > 0 ? ` · ${recipeCount} 标准方案` : ''}</span>
        <span className="akw-cfg__more">详情</span>
      </div>
    </article>
  )
}

export function ConfigToolsTab() {
  const [groups, setGroups] = useState<Group[]>([])
  const [guide, setGuide] = useState<GuideInfo | null>(null)
  const [recipesGuide, setRecipesGuide] = useState<RecipeGuideInfo | null>(null)
  const [actionMap, setActionMap] = useState<ActionMapInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [sectionId, setSectionId] = useState<SectionId>('model')
  const [subTab, setSubTab] = useState('')
  const [detailId, setDetailId] = useState('')
  /** 写入前是否弹审批（页面开关）；env 非 null 表示被环境变量强制，勾选框锁住。 */
  const [requireApproval, setRequireApproval] = useState<boolean | null>(null)
  const [approvalEnv, setApprovalEnv] = useState<string | null>(null)
  const [savingApproval, setSavingApproval] = useState(false)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const payload = await api.configCapabilities()
        if (!alive) return
        setGroups((payload.groups ?? []) as unknown as Group[])
        setGuide(payload.guide ?? null)
        setRecipesGuide(payload.recipesGuide ?? null)
        setActionMap(payload.actionMap ?? null)
        setRequireApproval(payload.requireApproval)
        setApprovalEnv(payload.approvalEnv ?? null)
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

  const byId = useMemo(() => {
    const map = new Map<string, Capability>()
    for (const group of groups) for (const item of group.items) map.set(item.id, item)
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

  const detail = detailId === '' ? null : (byId.get(detailId) ?? null)

  function pick(next: SectionId): void {
    setSectionId(next)
    setSubTab('')
    setDetailId('')
  }

  /** 页面开关：写入前是否弹审批。环境变量在时不让人改（host 侧也以环境变量优先）。 */
  async function toggleApproval(next: boolean): Promise<void> {
    setSavingApproval(true)
    try {
      const result = await api.configPrefs(next)
      setRequireApproval(result.requireApproval)
      setApprovalEnv(result.env)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSavingApproval(false)
    }
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

        <div className="akw-cfg__approval">
          <label>
            <input
              type="checkbox"
              checked={requireApproval !== false}
              disabled={savingApproval || approvalEnv !== null}
              onChange={(event) => void toggleApproval(event.target.checked)}
            />
            <b>写入前弹出审批</b>
          </label>
          <em>
            {approvalEnv !== null
              ? `环境变量已强制：${approvalEnv}`
              : '关掉后 agent 的写入不再询问（只建议本机调试时关）。'}
          </em>
        </div>

        {section.tabs !== undefined ? (
          <div className="akw-cfg__subtabs" role="group" aria-label={`${section.label}分类`}>
            {section.tabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                aria-pressed={activeTab === tab.key}
                className={`akw-cfg__subtab${activeTab === tab.key ? ' is-active' : ''}`}
                onClick={() => { setSubTab(tab.key); setDetailId('') }}
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
              <CapabilityCard key={item.id} item={item} onOpen={() => setDetailId(item.id)} />
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

      {detail !== null ? (
        <CapabilityDialog
          item={detail}
          guide={guide}
          recipesGuide={recipesGuide}
          actionMap={actionMap}
          onClose={() => setDetailId('')}
        />
      ) : null}
    </div>
  )
}
