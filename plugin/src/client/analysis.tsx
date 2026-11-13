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
  buildAnalysisDrawio,
  buildAnalysisMarkdown,
  buildAnalysisSvg,
} from '../shared/analysis-artifacts.ts'
import { workflowStepTypeLabel } from '../shared/workflow-labels.ts'
import { api, type AnalysisActionView, type AnalysisHistoryView, type AnalysisResultView } from './api.ts'

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

function downloadText(fileName: string, text: string, type: string): void {
  downloadBlob(fileName, new Blob([text], { type }))
}

function downloadPng(result: AnalysisResultView): Promise<void> {
  const svg = buildAnalysisSvg(result)
  const svgUrl = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }))
  return new Promise<void>((resolve, reject) => {
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
          downloadBlob(analysisArtifactName(result, 'png'), blob)
          resolve()
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

function ArtifactButtons({ result }: { result: AnalysisResultView }) {
  return (
    <div className="akw-analysis__downloads">
      <button
        className="akw-btn akw-btn--sm"
        type="button"
        onClick={() => {
          downloadText(
            analysisArtifactName(result, 'md'),
            buildAnalysisMarkdown(result),
            'text/markdown;charset=utf-8',
          )
        }}
      >
        下载文档
      </button>
      <button
        className="akw-btn akw-btn--sm"
        type="button"
        onClick={() => {
          downloadText(
            analysisArtifactName(result, 'drawio'),
            buildAnalysisDrawio(result),
            'application/vnd.jgraph.mxfile;charset=utf-8',
          )
        }}
      >
        下载 drawio
      </button>
      <button
        className="akw-btn akw-btn--sm"
        type="button"
        title="总览图 PNG，便于直接插入文档"
        onClick={() => {
          void downloadPng(result).catch((error: unknown) => {
            console.error('下载 PNG 失败', error)
          })
        }}
      >
        下载 PNG
      </button>
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
        if (action.targetStatusId !== null) return `${action.behaviorLabel} → 本对象 ${action.targetStatusName ?? action.targetStatusId}`
        if (action.relatedObjectId !== null) return `${action.behaviorLabel} → 关联对象 ${action.relatedStatusId ?? ''}`
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
        <div><b>{result.workflows.length}</b><span>工作流</span></div>
        <div><b>{result.enterCondition.configured ? result.enterCondition.count : '—'}</b><span>进入条件</span></div>
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
    </div>
  )
}

function ObjectCard({ entry, open }: { entry: AnalysisEntry; open: boolean }) {
  return (
    <details className="akw-analysis__object-card" open={open}>
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
          <div><dt>工作流</dt><dd>{result.workflows.length}</dd></div>
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
  const [historyError, setHistoryError] = useState('')

  async function refreshHistory(): Promise<void> {
    try {
      const payload = await api.analysisHistory()
      setHistory(payload.items)
      setHistoryError('')
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : String(error))
    }
  }

  useEffect(() => {
    void refreshHistory()
  }, [])

  async function openHistory(item: AnalysisHistoryView): Promise<void> {
    if (loading) return
    setLoading(true)
    setHistoryError('')
    try {
      const result = await api.analysisHistoryItem(item.id)
      setEntries([{ query: item.query, result, error: '' }])
      setQuery(item.query)
      setInputError('')
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : String(error))
    } finally {
      setLoading(false)
    }
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
          <em>点一条即可打开当时的分析结果，不用重新请求</em>
        </summary>
        {historyError !== '' ? <p className="akw-analysis__history-error">{historyError}</p> : null}
        {history.length === 0 ? (
          <p className="akw-analysis__history-empty">还没有历史；每次分析成功后会自动留一份。</p>
        ) : (
          <div className="akw-analysis__history-list">
            {history.slice(0, 12).map((item) => (
              <button
                key={item.id}
                type="button"
                className="akw-analysis__history-item"
                disabled={loading}
                onClick={() => void openHistory(item)}
              >
                <span>{historyTime(item.createdAt)}</span>
                <b>{item.objectName}</b>
                <code>{item.objectCode}</code>
                <em>
                  {item.enabledStatuses} 状态 · {item.userActions + item.enterActions} 动作 · {item.workflows} 工作流
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

      {entries.length === 0 ? (
        <div className="akw-analysis__empty">
          <b>还没有分析结果</b>
          <span>输入对象 id 或 code 后点「分析」。只读，不会修改平台配置。</span>
        </div>
      ) : (
        <>
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
              <ObjectCard key={entry.query} entry={entry} open={single} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
