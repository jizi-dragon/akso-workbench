/**
 * 「对象分析」页：输入对象 id / code，读生命周期状态、用户动作、进入动作。
 *
 * 本页只做只读分析：不写配置、不改状态。真正的数据读取在 host 的
 * `analysis/object`，这里只负责输入、空态和结果展示。
 */
import { useState, type FormEvent } from 'react'

import { api, type AnalysisActionView, type AnalysisResultView } from './api.ts'

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

export function ObjectAnalysisTab() {
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<AnalysisResultView | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function run(value: string) {
    const text = value.trim()
    if (text === '') {
      setError('请输入对象 id 或 code')
      return
    }
    setLoading(true)
    setError('')
    try {
      setResult(await api.analyzeObject(text))
    } catch (err) {
      setResult(null)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void run(query)
  }

  return (
    <div className="akw-analysis">
      <section className="akw-analysis__hero">
        <div>
          <h2>对象分析</h2>
          <p>输入对象 id 或 code，读取生命周期状态、状态上的用户动作与进入动作，以及进入条件。</p>
        </div>
        <form className="akw-analysis__form" onSubmit={submit}>
          <input
            className="akw-analysis__input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="对象 id 或 code，如 deviationmanagement__c"
            aria-label="对象 id 或 code"
            spellCheck={false}
            autoComplete="off"
          />
          <button className="akw-btn akw-btn--primary" type="submit" disabled={loading}>
            {loading ? '分析中…' : '分析'}
          </button>
        </form>
        <div className="akw-analysis__examples">
          <span>示例：</span>
          {['homework2__c', 'deviationmanagement__c', 'audit_plan__c'].map((item) => (
            <button
              key={item}
              type="button"
              className="akw-chip"
              onClick={() => {
                setQuery(item)
                void run(item)
              }}
            >
              {item}
            </button>
          ))}
        </div>
      </section>

      {error !== '' ? <div className="akw-analysis__error">{error}</div> : null}

      {result === null ? (
        <div className="akw-analysis__empty">
          <b>还没有分析结果</b>
          <span>输入对象 id 或 code 后点「分析」。只读，不会修改平台配置。</span>
        </div>
      ) : (
        <>
          <section className="akw-analysis__summary">
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
              <div><b>{result.enterCondition.configured ? result.enterCondition.count : '—'}</b><span>进入条件</span></div>
            </div>
          </section>

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
        </>
      )}
    </div>
  )
}
