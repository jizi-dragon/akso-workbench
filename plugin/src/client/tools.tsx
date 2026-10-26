/**
 * 「配置工具」导航页（挂在 akso-workbench 页面内，不单独占左栏）。
 *
 * 定位：**工具清单 / 台账**，不是执行台。
 *   - 只读能力目录（GET config/capabilities），不需要登录 eGMP、不写任何配置；
 *   - 按域分组，用数据卡片列出「插件里到底有哪些配置写入工具」；
 *   - 供统计与维护：总数、按阶段、按风险、受阻/未实测一目了然；
 *   - 点开卡片可看参数表（名称/位置/必填），便于后续加功能时对照。
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

const DOMAIN_LABEL: Record<string, string> = {
  auth: '认证',
  query: '查询',
  picklist: '选项集',
  object: '对象',
  lifecycle: '生命周期',
  field: '字段',
  layout: '表单布局',
  listlayout: '列表布局',
  menu: '菜单',
}

const PHASE: Record<string, string> = {
  auth: '基础',
  query: '查询',
  picklist: 'P1',
  object: 'P1',
  lifecycle: 'P1',
  field: 'P1',
  layout: 'P2',
  listlayout: 'P2',
  menu: 'P2',
}

/** 台账状态：只影响展示，不改变引擎行为（引擎对受阻项在 plan 阶段就拒绝）。 */
const STATUS: Record<string, { label: string; color: string; note: string }> = {
  'layout.save': { label: '受阻', color: '#b00020', note: '平台要求 section.belongId 由 FullPath 生成，规则待录制' },
  'menu.submit': { label: '未实测', color: '#b26a00', note: '平台没有按 code 预检菜单的接口，尚未在测试环境验证' },
  'menu.sort': { label: '未实测', color: '#b26a00', note: '全量替换菜单树，尚未在测试环境验证' },
  'lifecycle.status.create': { label: '未实测', color: '#b26a00', note: '需要对象先开启生命周期（不可逆），尚未验证' },
  'lifecycle.status.update': { label: '未实测', color: '#b26a00', note: '需要对象先开启生命周期（不可逆），尚未验证' },
}

const RISK_LABEL: Record<string, string> = { low: '低风险', medium: '中风险', high: '高风险' }
const RISK_COLOR: Record<string, string> = { low: '#2e7d32', medium: '#b26a00', high: '#b00020' }

function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span style={{ fontSize: 11, lineHeight: '16px', padding: '1px 7px', borderRadius: 9, color, border: `1px solid ${color}55`, background: `${color}11`, whiteSpace: 'nowrap' }}>
      {label}
    </span>
  )
}

const mono: React.CSSProperties = { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' }

const cardStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 8,
  padding: '12px 14px',
  borderRadius: 10,
  border: '1px solid var(--akw-line, #0002)',
  background: 'var(--akw-card, transparent)',
  minHeight: 132,
  textAlign: 'left',
  cursor: 'pointer',
}

type PhaseFilter = 'all' | 'P1' | 'P2' | 'query'

export function ConfigToolsTab() {
  const [groups, setGroups] = useState<Group[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<PhaseFilter>('all')
  const [openId, setOpenId] = useState('')

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const response = await fetch(new URL(`${BASE}/capabilities`, document.baseURI).toString(), { cache: 'no-store' })
        const payload = (await response.json()) as { groups?: Group[] }
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        if (alive) setGroups(payload.groups ?? [])
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => { alive = false }
  }, [])

  const all = useMemo(() => groups.flatMap((g) => g.items), [groups])
  const writeTools = useMemo(() => all.filter((c) => c.domain !== 'auth' && c.domain !== 'query'), [all])
  const blocked = useMemo(() => writeTools.filter((c) => STATUS[c.id]?.label === '受阻'), [writeTools])
  const untested = useMemo(() => writeTools.filter((c) => STATUS[c.id]?.label === '未实测'), [writeTools])
  const riskCount = useMemo(() => ({
    high: writeTools.filter((c) => c.risk === 'high').length,
    medium: writeTools.filter((c) => c.risk === 'medium').length,
    low: writeTools.filter((c) => c.risk === 'low').length,
  }), [writeTools])

  const visible = useMemo(() => {
    if (filter === 'all') return groups
    if (filter === 'query') return groups.filter((g) => g.domain === 'auth' || g.domain === 'query')
    return groups.filter((g) => PHASE[g.domain] === filter)
  }, [groups, filter])

  const filters: Array<{ key: PhaseFilter; label: string; count: number }> = [
    { key: 'all', label: '全部', count: all.length },
    { key: 'P1', label: 'P1', count: writeTools.filter((c) => PHASE[c.domain] === 'P1').length },
    { key: 'P2', label: 'P2', count: writeTools.filter((c) => PHASE[c.domain] === 'P2').length },
    { key: 'query', label: '查询/认证', count: all.length - writeTools.length },
  ]

  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
        {[
          { label: '配置写入工具', value: writeTools.length, color: 'inherit' },
          { label: '高风险', value: riskCount.high, color: RISK_COLOR.high },
          { label: '中风险', value: riskCount.medium, color: RISK_COLOR.medium },
          { label: '受阻', value: blocked.length, color: '#b00020' },
          { label: '未实测', value: untested.length, color: '#b26a00' },
        ].map((item) => (
          <div key={item.label} style={{ minWidth: 104, padding: '8px 12px', borderRadius: 10, border: '1px solid var(--akw-line, #0002)' }}>
            <div style={{ fontSize: 20, fontWeight: 650, color: item.color, lineHeight: 1.2 }}>{item.value}</div>
            <div style={{ fontSize: 11, opacity: 0.65 }}>{item.label}</div>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {filters.map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => setFilter(f.key)}
            style={{
              fontSize: 12,
              padding: '4px 10px',
              borderRadius: 14,
              cursor: 'pointer',
              border: `1px solid ${filter === f.key ? 'var(--akw-brand, #366ef4)' : 'var(--akw-line, #0003)'}`,
              background: filter === f.key ? 'var(--akw-brand-weak, #366ef414)' : 'transparent',
              color: 'inherit',
            }}
          >
            {f.label} <span style={{ opacity: 0.6 }}>{f.count}</span>
          </button>
        ))}
      </div>

      {loading ? <div style={{ fontSize: 12, opacity: 0.6 }}>加载中…</div> : null}
      {error !== '' ? <div style={{ fontSize: 12, color: '#b00020' }}>工具清单加载失败：{error}</div> : null}

      {visible.map((group) => (
        <div key={group.domain} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <h3 style={{ fontSize: 13, margin: 0 }}>{DOMAIN_LABEL[group.domain] ?? group.domain}</h3>
            <span style={{ fontSize: 11, opacity: 0.55 }}>{group.items.length} 个</span>
            <span style={{ ...mono, fontSize: 10, opacity: 0.35 }}>{group.domain}</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 10 }}>
            {group.items.map((item) => {
              const status = STATUS[item.id]
              const open = openId === item.id
              return (
                <article
                  key={item.id}
                  style={{ ...cardStyle, borderColor: status?.label === '受阻' ? '#b0002055' : 'var(--akw-line, #0002)' }}
                  onClick={() => setOpenId(open ? '' : item.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') setOpenId(open ? '' : item.id) }}
                >
                  <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
                    <strong style={{ fontSize: 13, lineHeight: 1.35 }}>{item.title}</strong>
                    <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
                      {item.irreversible === true ? <Badge label="不可逆" color="#b00020" /> : null}
                      <Badge label={RISK_LABEL[item.risk]} color={RISK_COLOR[item.risk]} />
                    </div>
                  </div>

                  <div style={{ ...mono, fontSize: 10.5, opacity: 0.55 }}>{item.id}</div>
                  <div style={{ ...mono, fontSize: 10.5, opacity: 0.75, wordBreak: 'break-all' }}>
                    <span style={{ opacity: 0.55 }}>{item.method}</span> {item.path}
                  </div>

                  <p style={{ fontSize: 11.5, lineHeight: 1.5, margin: 0, opacity: 0.72, display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                    {item.description}
                  </p>

                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 'auto', flexWrap: 'wrap' }}>
                    <Badge label={PHASE[item.domain] ?? '查询'} color="#5b6b8c" />
                    {status === undefined ? <Badge label="已实现" color="#2e7d32" /> : <Badge label={status.label} color={status.color} />}
                    <span style={{ fontSize: 10.5, opacity: 0.5 }}>{item.params.length} 个参数</span>
                  </div>

                  {open ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, borderTop: '1px solid var(--akw-line, #0002)', paddingTop: 8 }}>
                      {status !== undefined ? <div style={{ fontSize: 11, color: status.color }}>{status.note}</div> : null}
                      {item.params.length === 0 ? <div style={{ fontSize: 11, opacity: 0.5 }}>无参数</div> : (
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                          <thead>
                            <tr style={{ opacity: 0.55, textAlign: 'left' }}>
                              <th style={{ fontWeight: 500, padding: '2px 4px' }}>参数</th>
                              <th style={{ fontWeight: 500, padding: '2px 4px' }}>位置</th>
                              <th style={{ fontWeight: 500, padding: '2px 4px' }}>必填</th>
                            </tr>
                          </thead>
                          <tbody>
                            {item.params.map((p) => (
                              <tr key={p.name}>
                                <td style={{ ...mono, padding: '2px 4px' }}>{p.name}</td>
                                <td style={{ padding: '2px 4px', opacity: 0.65 }}>{p.location}</td>
                                <td style={{ padding: '2px 4px', opacity: 0.65 }}>{p.required === true ? '是' : '否'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  ) : null}
                </article>
              )
            })}
          </div>
        </div>
      ))}

      {!loading && visible.length === 0 ? <div style={{ fontSize: 12, opacity: 0.6 }}>当前筛选下没有工具。</div> : null}
    </section>
  )
}
