/**
 * 「对象分析」页：输入 1–3 个对象 id / code，读生命周期状态、用户动作、进入动作。
 *
 * 本页只做只读分析：不写配置、不改状态。真正的数据读取在 host 的
 * `analysis/object`，这里只负责输入、并发请求、空态和结果展示。
 *
 * 多对象时先给一张「对比总览」，下面每个对象是可展开的明细；单对象直接展开，
 * 保持原来的阅读路径。
 */
import { useEffect, useState, type FormEvent } from 'react'

import { parseAnalysisQueries } from '../shared/analysis-query.ts'
import {
  analysisArtifactName,
  analysisOutputFolder,
  analysisPackageName,
  buildAnalysisDrawio,
  buildAnalysisMarkdown,
  buildAnalysisSvg,
} from '../shared/analysis-artifacts.ts'
import { zipStore } from './zip-store.ts'
import { workflowStepTypeLabel } from '../shared/workflow-labels.ts'
import { api, type AnalysisActionView, type AnalysisHistoryView, type AnalysisResultView } from './api.ts'
import { useFoldSet } from './view.tsx'

interface AnalysisEntry {
  query: string
  result: AnalysisResultView | null
  error: string
}

function ActionRow({ action }: { action: AnalysisActionView }) {
  const typeLabel = action.kind === 'user' ? '用户动作' : '进入动作'
  const configLabel =
    action.configCount === 0
      ? '未配置具体动作'
      : action.behaviorLabels.length > 0
        ? action.behaviorLabels.join(' / ')
        : '已配置'
  return (
    <div className="akw-action">
      <div className="akw-action__head">
        <span className={`akw-badge ${action.kind === 'user' ? 'is-user' : 'is-enter'}`}>{typeLabel}</span>
        <b>{action.name}</b>
        {action.code !== '' ? <code>{action.code}</code> : null}
      </div>
      <div className="akw-action__effect">
        <span>{configLabel}</span>
        {action.workflowName !== null ? <span>工作流：{action.workflowName}</span> : null}
      </div>
      {action.workflowId !== null ? <div className="akw-action__id">workflowId：{action.workflowId}</div> : null}
      {action.description !== '' ? <p className="akw-action__desc">{action.description}</p> : null}
    </div>
  )
}

function downloadBlob(fileName: string, blob: Blob): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  document.body.appendChild(link)
  link.click()
  link.remove()
  // 下载是异步开始的；立即 revoke 在部分 Chrome 版本会把 Blob 提前断掉。
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function renderPngBlob(result: AnalysisResultView): Promise<Blob> {
  const svg = buildAnalysisSvg(result)
  const svgUrl = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }))
  return new Promise<Blob>((resolve, reject) => {
    const image = new Image()
    image.decoding = 'async'
    image.onload = () => {
      try {
        const canvas = document.createElement('canvas')
        canvas.width = image.naturalWidth || 1180
        canvas.height = image.naturalHeight || 800
        const context = canvas.getContext('2d')
        if (context === null) {
          reject(new Error('浏览器不支持 canvas'))
          return
        }
        context.fillStyle = '#ffffff'
        context.fillRect(0, 0, canvas.width, canvas.height)
        context.drawImage(image, 0, 0)
        canvas.toBlob((blob) => {
          if (blob === null) {
            reject(new Error('PNG 生成失败'))
            return
          }
          resolve(blob)
        }, 'image/png')
      } catch (error) {
        reject(error instanceof Error ? error : new Error(String(error)))
      } finally {
        URL.revokeObjectURL(svgUrl)
      }
    }
    image.onerror = () => {
      URL.revokeObjectURL(svgUrl)
      reject(new Error('SVG 预览加载失败'))
    }
    image.src = svgUrl
  })
}

async function buildPackageBlob(result: AnalysisResultView): Promise<{ name: string; blob: Blob }> {
  const generatedAt = new Date()
  const folder = analysisOutputFolder(result, generatedAt)
  const encoder = new TextEncoder()
  const png = await renderPngBlob(result)
  const files = [
    { name: `${folder}/`, data: new Uint8Array(0) },
    { name: `${folder}/${analysisArtifactName(result, 'md', generatedAt)}`, data: encoder.encode(buildAnalysisMarkdown(result, { generatedAt })) },
    { name: `${folder}/${analysisArtifactName(result, 'drawio', generatedAt)}`, data: encoder.encode(buildAnalysisDrawio(result, { generatedAt })) },
    { name: `${folder}/${analysisArtifactName(result, 'png', generatedAt)}`, data: new Uint8Array(await png.arrayBuffer()) },
  ]
  return {
    name: analysisPackageName(result, generatedAt),
    blob: new Blob([zipStore(files, generatedAt)], { type: 'application/zip' }),
  }
}

function ArtifactButtons({ result }: { result: AnalysisResultView }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function handlePackage(): Promise<void> {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const { name, blob } = await buildPackageBlob(result)
      downloadBlob(name, blob)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="akw-analysis__downloads">
      <button className="akw-btn akw-btn--sm" type="button" disabled={busy} onClick={() => void handlePackage()}>
        {busy ? '打包中…' : '下载分析包（.zip）'}
      </button>
      <span className="akw-analysis__package-hint">必含 md / drawio / png，解压后是一个秒级时间戳文件夹</span>
      {error !== '' ? <span className="akw-analysis__package-error">{error}</span> : null}
    </div>
  )
}

function workflowTriggerLabel(workflow: AnalysisResultView['workflows'][number]): string {
  if (workflow.triggeredBy.length > 0) {
    return workflow.triggeredBy
      .map((trigger) => `${trigger.statusName} / ${trigger.actionName}`)
      .join('；')
  }
  if (workflow.statusBindings.length > 0) {
    return `状态绑定：${workflow.statusBindings.map((binding) => binding.statusName).join('、')}`
  }
  return '触发点未反查到'
}

function workflowStepEffect(step: AnalysisResultView['workflows'][number]['steps'][number]): string {
  if (step.task !== null) {
    const bits = [step.task.isNotifyTaskType ? '通知任务' : '任务']
    if (step.task.isRequireApproval) bits.push('需审批')
    if (step.task.approvals.length > 0) bits.push(`审批：${step.task.approvals.join('/')}`)
    return bits.join(' · ')
  }
  if (step.decision !== null) {
    return `判断 · ${step.decision.rules.length} 条规则${step.decision.elseNextStepId === null ? '' : ' · 有否则分支'}`
  }
  if (step.actions.length > 0) {
    return step.actions
      .map((action) => {
        if (action.targetStatusId !== null) return `${action.behaviorLabel} → 本对象状态「${action.targetStatusName ?? action.targetStatusId}」`
        if (action.relatedObjectId !== null) {
          const object = action.relatedObjectName ?? action.relatedObjectCode ?? action.relatedObjectId
          return `${action.behaviorLabel} → 【${object}】状态「${action.relatedStatusName ?? action.relatedStatusId ?? '未读'}」`
        }
        if (action.workflowId !== null) return `${action.behaviorLabel} → 再启工作流`
        return action.behaviorLabel
      })
      .join('；')
  }
  return '—'
}

function WorkflowRow({ workflow }: { workflow: AnalysisResultView['workflows'][number] }) {
  return (
    <details className="akw-workflow">
      <summary className="akw-workflow__summary">
        <div>
          <b>{workflow.name}</b>
          <code>{workflow.code}</code>
        </div>
        <span className="akw-workflow__meta">
          {workflow.steps.length} 步 · status {workflow.status} · v{workflow.versionNo}
        </span>
      </summary>
      <div className="akw-workflow__body">
        <div className="akw-workflow__trigger">
          <span>触发：{workflowTriggerLabel(workflow)}</span>
          <span>允许自动启动：{workflow.isAllowTrigger ? '是' : '否'}</span>
        </div>
        <table className="akw-workflow__table">
          <thead>
            <tr>
              <th>#</th>
              <th>类型</th>
              <th>名称</th>
              <th>配置</th>
            </tr>
          </thead>
          <tbody>
            {workflow.steps.map((step) => (
              <tr key={step.id}>
                <td>{step.sort}</td>
                <td>{workflowStepTypeLabel(step.stepType)}</td>
                <td>
                  <div>{step.name}</div>
                  {step.code !== '' ? <code>{step.code}</code> : null}
                </td>
                <td>{workflowStepEffect(step)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {workflow.warnings.length > 0 ? (
          <ul className="akw-workflow__warnings">
            {workflow.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </details>
  )
}


function fieldReference(field: AnalysisResultView['fields'][number]): string {
  if (field.referenceObjectCode !== null) {
    return `${field.referenceObjectName ?? field.referenceObjectCode}（${field.referenceObjectCode}）`
  }
  if (field.picklistId !== null) {
    return field.picklistName ?? field.picklistId
  }
  return '—'
}

function FieldTable({ fields }: { fields: AnalysisResultView['fields'] }) {
  const enabled = fields.filter((field) => field.isEnabled).length
  const required = fields.filter((field) => field.isRequired).length
  const custom = fields.filter((field) => field.source === 3).length
  /** 字段多的时候先给一个可读高度，需要横向核对时再整表展开。 */
  const [expanded, setExpanded] = useState(false)
  return (
    <section className="akw-fields">
      <div className="akw-fields__head">
        <h3>字段（{fields.length}）</h3>
        <div className="akw-fields__headRight">
          <p>
            启用 {enabled} · 停用 {fields.length - enabled} · 必填 {required} · 自定义 {custom}
          </p>
          {fields.length === 0 ? null : (
            <button
              type="button"
              className="akw-btn akw-btn--sm akw-btn--ghost"
              aria-expanded={expanded}
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? '收起字段表' : '展开全部'}
            </button>
          )}
        </div>
      </div>
      {fields.length === 0 ? (
        <p className="akw-fields__empty">没有读到字段。</p>
      ) : (
        <div className={`akw-fields__scroll${expanded ? ' is-expanded' : ''}`}>
          <table className="akw-fields__table">
            <thead>
              <tr>
                <th>#</th>
                <th>字段名</th>
                <th>code</th>
                <th>类型</th>
                <th>必填</th>
                <th>唯一</th>
                <th>来源</th>
                <th>引用 / 选项集</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {fields.map((field, index) => (
                <tr key={field.id}>
                  <td>{index + 1}</td>
                  <td>{field.name}</td>
                  <td>
                    <code>{field.code}</code>
                  </td>
                  <td>{field.dataTypeLabel}</td>
                  <td>{field.isRequired ? '是' : '—'}</td>
                  <td>{field.isUnique ? '是' : '—'}</td>
                  <td>{field.sourceLabel}</td>
                  <td title={field.referenceObjectId ?? field.picklistId ?? ''}>{fieldReference(field)}</td>
                  <td>{field.isEnabled ? '启用' : '停用'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function MatrixTable({ matrices }: { matrices: AnalysisResultView['matrices'] }) {
  const enabled = matrices.filter((matrix) => matrix.isEnabled).length
  const missingMenu = matrices.filter((matrix) => !matrix.menuFound).length
  const emptyData = matrices.filter((matrix) => matrix.recordCount === 0).length
  if (matrices.length === 0) return null
  return (
    <section className="akw-fields">
      <div className="akw-fields__head">
        <h3>审批矩阵（{matrices.length}）</h3>
        <div className="akw-fields__headRight">
          <span>启用 {enabled} · 无菜单 {missingMenu} · 无数据 {emptyData}</span>
        </div>
      </div>
      <div className="akw-fields__scroll is-expanded">
        <table className="akw-fields__table">
          <thead>
            <tr>
              <th>审批矩阵</th>
              <th>状态</th>
              <th>使用位置</th>
              <th>菜单</th>
              <th>业务数据</th>
              <th>异常</th>
            </tr>
          </thead>
          <tbody>
            {matrices.map((matrix) => (
              <tr key={matrix.id}>
                <td>
                  <b>{matrix.name}</b>
                  <div><code>{matrix.code || matrix.id}</code></div>
                </td>
                <td>{matrix.isEnabled ? '启用' : `停用（${matrix.status}）`}</td>
                <td>
                  {matrix.usage.map((item) => (
                    <div key={`${item.workflowId}-${item.controlId}`}>
                      {item.workflowName} / {item.controlName}
                    </div>
                  ))}
                </td>
                <td>{matrix.menuFound ? matrix.menus.map((menu) => menu.path).join('；') : '缺失'}</td>
                <td>{matrix.recordCount === null ? '未读到' : matrix.recordCount}</td>
                <td>{matrix.warnings.length > 0 ? matrix.warnings.join('；') : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function ObjectSummary({ entry }: { entry: AnalysisEntry }) {
  const result = entry.result
  if (result === null) {
    return (
      <summary className="akw-analysis__summary is-error">
        <div className="akw-analysis__object">
          <h3>{entry.query}</h3>
          <div className="akw-analysis__meta">
            <span>分析失败</span>
          </div>
        </div>
        <span className="akw-analysis__expand">查看错误</span>
      </summary>
    )
  }
  const fieldCount = result.fields?.length ?? result.totals.fields ?? 0
  return (
    <summary className="akw-analysis__summary">
      <div className="akw-analysis__object">
        <h3>{result.object.name}</h3>
        <div className="akw-analysis__meta">
          <code>{result.object.code}</code>
          <span>对象 id：{result.object.id}</span>
        </div>
        <div className="akw-analysis__meta">
          <span>生命周期：{result.lifecycle?.name || '—'}</span>
          <span>{result.lifecycle?.id ?? result.object.lifecycleId}</span>
        </div>
      </div>
      <div className="akw-analysis__stats">
        <div><b>{result.totals.enabledStatuses}</b><span>启用状态</span></div>
        <div><b>{result.totals.userActions}</b><span>用户动作</span></div>
        <div><b>{result.totals.enterActions}</b><span>进入动作</span></div>
        <div><b>{fieldCount}</b><span>字段</span></div>
        <div><b>{result.workflows.length}</b><span>工作流</span></div>
        <div><b>{(result.matrices ?? []).length}</b><span>审批矩阵</span></div>
      </div>
      <span className="akw-analysis__expand">明细</span>
    </summary>
  )
}

function ObjectDetail({ result }: { result: AnalysisResultView }) {
  return (
    <div className="akw-analysis__detail">
      <ArtifactButtons result={result} />

      {result.enterCondition.readable && !result.enterCondition.configured ? (
        <div className="akw-analysis__notice">生命周期进入条件：未配置（StatusEnterRule/GetList 返回空）。</div>
      ) : null}

      {result.warnings.length > 0 ? (
        <ul className="akw-analysis__warnings">
          {result.warnings.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}

      <section className="akw-analysis__workflows">
        <div className="akw-analysis__workflows-head">
          <h3>工作流（{result.workflows.length}）</h3>
          <p>状态动作触发到的流程，以及对象上未直接反查到触发点的流程；逐步展开可看任务/判断/动作。</p>
        </div>
        {result.workflows.length === 0 ? (
          <p className="akw-analysis__workflows-empty">没有读到工作流。</p>
        ) : (
          result.workflows.map((workflow) => <WorkflowRow key={workflow.basicId} workflow={workflow} />)
        )}
      </section>

      <div className="akw-status-grid">
        {result.statuses.map((status) => (
          <section className="akw-status" key={status.id}>
            <header className="akw-status__head">
              <div>
                <b>{status.name}</b>
                <code>{status.code}</code>
              </div>
              <span>sort {status.sort}</span>
            </header>
            <div className="akw-status__counts">
              <span>用户动作 {status.userActions.length}</span>
              <span>进入动作 {status.enterActions.length}</span>
            </div>
            {status.userActions.length + status.enterActions.length === 0 ? (
              <p className="akw-status__none">无动作</p>
            ) : (
              <div className="akw-status__actions">
                {[...status.userActions, ...status.enterActions].map((action) => (
                  <ActionRow key={`${action.kind}-${action.id}`} action={action} />
                ))}
              </div>
            )}
          </section>
        ))}
      </div>

      <MatrixTable matrices={result.matrices ?? []} />
      <FieldTable fields={result.fields ?? []} />
    </div>
  )
}

function ObjectCard({ entry, open, onToggle }: { entry: AnalysisEntry; open: boolean; onToggle: (value: boolean) => void }) {
  return (
    <details
      className="akw-analysis__object-card"
      open={open}
      onToggle={(event) => {
        const next = event.currentTarget.open
        if (next !== open) onToggle(next)
      }}
    >
      <ObjectSummary entry={entry} />
      {entry.result === null ? (
        <div className="akw-analysis__detail">
          <div className="akw-analysis__error">{entry.error}</div>
        </div>
      ) : (
        <ObjectDetail result={entry.result} />
      )}
    </details>
  )
}

function ComparisonCard({ entry }: { entry: AnalysisEntry }) {
  const result = entry.result
  return (
    <article className={`akw-analysis__compare-card${result === null ? ' is-error' : ''}`}>
      <div className="akw-analysis__compare-head">
        <div>
          <b>{result?.object.name ?? entry.query}</b>
          <code>{result?.object.code ?? entry.query}</code>
        </div>
        {result === null ? <span className="akw-analysis__compare-bad">失败</span> : null}
      </div>
      {result === null ? (
        <p className="akw-analysis__compare-error">{entry.error}</p>
      ) : (
        <dl className="akw-analysis__compare-list">
          <div><dt>生命周期</dt><dd>{result.lifecycle?.name || '—'}</dd></div>
          <div><dt>状态</dt><dd>{result.totals.enabledStatuses} / {result.totals.statuses}</dd></div>
          <div><dt>用户动作</dt><dd>{result.totals.userActions}</dd></div>
          <div><dt>进入动作</dt><dd>{result.totals.enterActions}</dd></div>
          <div><dt>字段</dt><dd>{result.fields?.length ?? result.totals.fields ?? 0}</dd></div>
          <div><dt>工作流</dt><dd>{result.workflows.length}</dd></div>
          <div><dt>审批矩阵</dt><dd>{(result.matrices ?? []).length}</dd></div>
          <div>
            <dt>进入条件</dt>
            <dd>{result.enterCondition.readable ? (result.enterCondition.configured ? '已配置' : '未配置') : '不可读'}</dd>
          </div>
          <div><dt>警告</dt><dd>{result.warnings.length}</dd></div>
        </dl>
      )}
      {result !== null ? <ArtifactButtons result={result} /> : null}
    </article>
  )
}

function historyTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

export function ObjectAnalysisTab() {
  const [query, setQuery] = useState('')
  const [entries, setEntries] = useState<AnalysisEntry[]>([])
  const [inputError, setInputError] = useState('')
  const [loading, setLoading] = useState(false)
  const [history, setHistory] = useState<AnalysisHistoryView[]>([])
  const [historyDir, setHistoryDir] = useState('')
  const [historyError, setHistoryError] = useState('')
  /** 对象卡的展开态：持久化，刷新或切模块回来仍是上次看到的层级。 */
  const folds = useFoldSet('analysis')
  const [notice, setNotice] = useState('')

  async function refreshHistory(): Promise<void> {
    try {
      const payload = await api.analysisHistory()
      setHistory(payload.items)
      if (typeof payload.dir === 'string') setHistoryDir(payload.dir)
      setHistoryError('')
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : String(error))
    }
  }

  useEffect(() => {
    void refreshHistory()
    const onFocus = (): void => {
      void refreshHistory()
    }
    const onVisible = (): void => {
      if (document.visibilityState === 'visible') void refreshHistory()
    }
    // agent 在后台调工具时页面收不到任何通知；轻量轮询让「历史」自己长出来。
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refreshHistory()
    }, 15_000)
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [])

  async function openHistory(item: AnalysisHistoryView): Promise<void> {
    if (loading) return
    setLoading(true)
    setHistoryError('')
    try {
      const detail = await api.analysisHistoryItem(item.id)
      const query = detail.query === '' ? item.query : detail.query
      const entry: AnalysisEntry =
        detail.status === 'failed' || detail.result === undefined
          ? { query, result: null, error: detail.error ?? '这次分析没有成功' }
          : { query, result: detail.result, error: '' }
      // 并入而不是替换：手头正在看的这份不会被一次历史点击挤掉，多份可同屏对照。
      setEntries((prev) => [entry, ...prev.filter((current) => current.query !== query)].slice(0, 8))
      folds.setOpen(query, true)
      setQuery(query)
      setInputError('')
      setNotice(`已并入历史记录「${query}」；“清空本次”可以只留这一份。`)
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : String(error))
    } finally {
      setLoading(false)
    }
  }

  function expandAll(): void {
    folds.setAll(entries.map((entry) => entry.query), true)
    setNotice('')
  }

  function collapseAll(): void {
    folds.setAll(entries.map((entry) => entry.query), false)
    setNotice('')
  }

  function clearEntries(): void {
    setEntries([])
    setNotice('')
  }

  async function run(value: string) {
    if (loading) return
    const parsed = parseAnalysisQueries(value)
    if (parsed.error !== '') {
      setInputError(parsed.error)
      setEntries([])
      return
    }

    setLoading(true)
    setInputError('')
    setNotice('')
    try {
      const next = await Promise.all(
        parsed.queries.map(async (item): Promise<AnalysisEntry> => {
          try {
            return { query: item, result: await api.analyzeObject(item), error: '' }
          } catch (err) {
            return { query: item, result: null, error: err instanceof Error ? err.message : String(err) }
          }
        }),
      )
      setEntries(next)
      void refreshHistory()
    } finally {
      setLoading(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void run(query)
  }

  function changeQuery(value: string) {
    setQuery(value)
    if (inputError !== '') setInputError('')
  }

  const successCount = entries.filter((entry) => entry.result !== null).length
  const single = entries.length === 1
  const source = entries.find((entry) => entry.result !== null)?.result?.source ?? null
  const artifacts = entries.find((entry) => entry.result?.artifacts !== undefined)?.result?.artifacts ?? null

  return (
    <div className="akw-analysis">
      <section className="akw-analysis__hero">
        <div>
          <h2>对象分析</h2>
          <p>输入对象 id 或 code，读取生命周期状态、状态动作、进入条件，并展开对象工作流。支持同时分析 1–3 个对象，可下载文档与 drawio 流程图。</p>
        </div>
        <form className="akw-analysis__form" onSubmit={submit}>
          <textarea
            className="akw-analysis__input akw-analysis__input--multi"
            value={query}
            onChange={(event) => changeQuery(event.target.value)}
            placeholder="每行一个对象 id / code；也可用逗号分隔，最多 3 个"
            aria-label="对象 id 或 code，最多 3 个"
            spellCheck={false}
            autoComplete="off"
            rows={2}
          />
          <button className="akw-btn akw-btn--primary" type="submit" disabled={loading}>
            {loading ? `分析中…${entries.length > 1 ? `（${entries.length}）` : ''}` : '分析'}
          </button>
        </form>
        <div className="akw-analysis__examples">
          <span>示例：</span>
          {['homework2__c', 'deviationmanagement__c', 'audit_plan__c'].map((item) => (
            <button
              key={item}
              type="button"
              className="akw-chip"
              disabled={loading}
              onClick={() => {
                setQuery(item)
                void run(item)
              }}
            >
              {item}
            </button>
          ))}
          <span className="akw-analysis__hint">多个对象用逗号分隔，最多 3 个。</span>
        </div>
      </section>

      <details className="akw-analysis__history" open={history.length > 0}>
        <summary>
          历史分析 <span>{history.length}</span>
          <em>点一条即并入下方的本次结果，不替换当前内容</em>
        </summary>
        <div className="akw-analysis__history-meta">
          <code title={historyDir}>{historyDir === '' ? '历史目录：加载中…' : `历史目录：${historyDir}`}</code>
          <button type="button" className="akw-btn akw-btn--sm" onClick={() => void refreshHistory()}>
            刷新
          </button>
        </div>
        {historyError !== '' ? <p className="akw-analysis__history-error">{historyError}</p> : null}
        {history.length === 0 ? (
          <p className="akw-analysis__history-empty">还没有历史；每次分析（成功或失败）都会自动留一份。</p>
        ) : (
          <div className="akw-analysis__history-list">
            {history.slice(0, 12).map((item) => (
              <button
                key={item.id}
                type="button"
                className={`akw-analysis__history-item${item.status === 'failed' ? ' is-failed' : ''}`}
                disabled={loading}
                onClick={() => void openHistory(item)}
              >
                <span>{historyTime(item.createdAt)}</span>
                {item.status === 'failed' ? (
                  <b className="akw-analysis__history-bad">失败</b>
                ) : (
                  <b>{item.objectName === '' ? item.objectCode : item.objectName}</b>
                )}
                <code>{item.objectCode === '' ? item.query : item.objectCode}</code>
                <em>
                  {item.status === 'failed'
                    ? (item.error ?? '分析失败')
                    : `${item.enabledStatuses} 状态 · ${item.userActions + item.enterActions} 动作 · ${item.fields ?? 0} 字段 · ${item.workflows} 工作流`}
                </em>
              </button>
            ))}
          </div>
        )}
      </details>

      {inputError !== '' ? <div className="akw-analysis__error">{inputError}</div> : null}

      {source !== null ? (
        <div className="akw-analysis__source">
          <span>本次分析环境</span>
          <code>{source.baseUrl}</code>
          <span>账号</span>
          <code>{source.account}</code>
        </div>
      ) : null}

      {artifacts !== null ? (
        <div className="akw-analysis__source">
          <span>分析产物</span>
          <code title={artifacts.folder}>{artifacts.folder}</code>
          <span>文件</span>
          <code>{artifacts.files.join(' / ')}</code>
        </div>
      ) : null}

      {entries.length === 0 ? (
        <div className="akw-analysis__empty">
          <b>还没有分析结果</b>
          <span>输入对象 id 或 code 后点「分析」。只读，不会修改平台配置。</span>
        </div>
      ) : (
        <>
          <div className="akw-analysis__toolbar">
            <span className="akw-analysis__toolbarCount">
              本次 {entries.length} 个对象 · {successCount} 成功
            </span>
            <div className="akw-analysis__toolbarActions">
              <button type="button" className="akw-btn akw-btn--sm akw-btn--ghost" onClick={expandAll}>
                展开全部
              </button>
              <button type="button" className="akw-btn akw-btn--sm akw-btn--ghost" onClick={collapseAll}>
                收起全部
              </button>
              <button type="button" className="akw-btn akw-btn--sm akw-btn--ghost" onClick={clearEntries}>
                清空本次
              </button>
            </div>
          </div>
          {notice !== '' ? (
            <p className="akw-analysis__hint is-notice" role="status">
              {notice}
            </p>
          ) : null}
          {entries.length > 1 ? (
            <section className="akw-analysis__compare">
              <div className="akw-analysis__compare-heading">
                <div>
                  <h3>对比总览</h3>
                  <p>先看规模与差异，展开下方对象看状态和动作明细。</p>
                </div>
                <span className="akw-analysis__compare-count">
                  {successCount} / {entries.length} 成功
                </span>
              </div>
              <div className="akw-analysis__compare-grid">
                {entries.map((entry) => (
                  <ComparisonCard key={entry.query} entry={entry} />
                ))}
              </div>
            </section>
          ) : null}

          <div className={`akw-analysis__objects${single ? ' is-single' : ''}`}>
            {entries.map((entry) => (
              <ObjectCard
                key={entry.query}
                entry={entry}
                open={folds.isOpen(entry.query, single)}
                onToggle={(value) => folds.setOpen(entry.query, value)}
              />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
