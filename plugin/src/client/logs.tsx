/**
 * 运行日志（页面）：把 host 侧 `journal/` 里的使用记录**一键带走**。
 *
 * 为什么单独一页：出问题的用户往往说不清「点了哪个按钮、报了什么错」，而日志在
 * `$DSH_HOME/akso-workbench/journal/` 里，普通人不会去翻。这里显示概览，再给
 * 「下载最近 7 天 / 30 天」——用户把文件发回来即可复盘。
 *
 * 三条边界（与 host 侧 journal.ts 对应）：
 *   · 不显示令牌/口令/请求体：下载文件里也没有（写盘前已打码）；
 *   · 概览只统计「最近保留期」，下载按天数截取，超 12 MB 会从最旧的丢；
 *   · 页面自己也有记录：本页的下载/刷新会写 `ui.*` 事件，便于确认用户操作过什么。
 */
import { useEffect, useState } from 'react'

import { api, downloadJournal, track, type JournalSummaryView } from './api.ts'

function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function fmtTime(value: string | null): string {
  if (value === null) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

/** 本地下载：Blob → objectURL → 隐藏的 <a>。1 秒后回收 URL（Safari 需要延迟）。 */
function saveBlob(fileName: string, blob: Blob): void {
  const href = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = fileName
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(href), 1000)
}

export function LogsTab() {
  const [summary, setSummary] = useState<JournalSummaryView | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [note, setNote] = useState<string | null>(null)

  async function load(): Promise<void> {
    setLoading(true)
    try {
      const result = await api.journalSummary()
      setSummary(result.summary)
      setError(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    track('ui.logs.open', {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function download(days: number): Promise<void> {
    setBusy(days)
    setNote(null)
    try {
      const file = await downloadJournal(days)
      saveBlob(file.fileName, file.blob)
      track('ui.journal.download', { days, events: file.events, truncated: file.truncated })
      setNote(
        file.truncated
          ? `已下载最近 ${days} 天（文件超过 12 MB，最旧的记录已裁掉；共 ${file.events} 条）`
          : `已下载最近 ${days} 天，共 ${file.events} 条记录`,
      )
      await load()
    } catch (cause) {
      setNote(`下载失败：${cause instanceof Error ? cause.message : String(cause)}`)
    } finally {
      setBusy(null)
    }
  }

  async function copyDir(): Promise<void> {
    if (summary === null) return
    try {
      await navigator.clipboard.writeText(summary.dir)
      setNote('日志目录已复制')
    } catch {
      setNote(`复制失败，请手动选中：${summary.dir}`)
    }
  }

  return (
    <div className="akw-logs">
      <div className="akw-logs__head">
        <div>
          <h2>运行日志</h2>
          <p>
            记录 agent 调用了哪些工具、页面请求了什么、审批/换票/分析等动作的结果。
            不记口令与令牌。遇到问题时下载日志发给支持同学，比口述「点了什么、报什么错」准确得多。
          </p>
        </div>
        <div className="akw-logs__actions">
          <button type="button" className="akw-btn akw-btn--sm" onClick={() => void load()} disabled={loading}>
            {loading ? '刷新中…' : '刷新'}
          </button>
          <button type="button" className="akw-btn akw-btn--sm" onClick={() => void copyDir()} disabled={summary === null}>
            复制日志目录
          </button>
        </div>
      </div>

      {error !== null ? (
        <div className="akw-notice akw-notice--err" role="alert">
          <span className="akw-notice__grow">读取日志概览失败：{error}</span>
        </div>
      ) : null}

      {note !== null ? (
        <div className="akw-notice" role="status" aria-live="polite">
          <span className="akw-notice__grow">{note}</span>
        </div>
      ) : null}

      <div className="akw-logs__stats">
        <span className="akw-stat">
          <b>{summary === null ? '—' : summary.events.toLocaleString('zh-CN')}</b>
          <span>条记录</span>
        </span>
        <span className="akw-stat">
          <b>{summary === null ? '—' : fmtBytes(summary.bytes)}</b>
          <span>占用空间</span>
        </span>
        <span className="akw-stat">
          <b>{summary === null ? '—' : `${summary.retentionDays} 天`}</b>
          <span>保留期</span>
        </span>
        <span className="akw-stat">
          <b>{summary === null ? '—' : summary.files.length}</b>
          <span>个日志文件</span>
        </span>
      </div>

      <dl className="akw-logs__meta">
        <div>
          <dt>日志目录</dt>
          <dd>
            <code>{summary?.dir ?? '—'}</code>
          </dd>
        </div>
        <div>
          <dt>最早记录</dt>
          <dd>{fmtTime(summary?.oldest ?? null)}</dd>
        </div>
        <div>
          <dt>最新记录</dt>
          <dd>{fmtTime(summary?.newest ?? null)}</dd>
        </div>
      </dl>

      <div className="akw-logs__downloads">
        <div className="akw-logs__download">
          <div>
            <b>最近 7 天</b>
            <span>排查近期问题够用；文件小，发回来快。</span>
          </div>
          <button
            type="button"
            className="akw-btn akw-btn--primary akw-btn--sm"
            onClick={() => void download(7)}
            disabled={busy !== null}
          >
            {busy === 7 ? '打包中…' : '下载 7 天日志'}
          </button>
        </div>
        <div className="akw-logs__download">
          <div>
            <b>最近 30 天</b>
            <span>问题已经过去一段时间、或需要看长期使用情况时选这个。</span>
          </div>
          <button
            type="button"
            className="akw-btn akw-btn--sm"
            onClick={() => void download(30)}
            disabled={busy !== null}
          >
            {busy === 30 ? '打包中…' : '下载 30 天日志'}
          </button>
        </div>
      </div>

      <section className="akw-logs__kinds">
        <h3>日志里有什么</h3>
        <ul>
          <li>
            <code>tool.invoke / tool.done / tool.fail</code> —— agent 调用了哪个工具（含 UID）、耗时、结果摘要；
          </li>
          <li>
            <code>http</code> —— 本机 API 的每条请求与响应码；
          </li>
          <li>
            <code>cloud.* / egmp.*</code> —— 登录、同步、换票、退出；
          </li>
          <li>
            <code>approval.* / plan.* / apply.*</code> —— 审批窗的等待与决定、计划生成与执行结果；
          </li>
          <li>
            <code>analysis.run</code> —— 对象分析的对象、环境、耗时与告警数；
          </li>
          <li>
            <code>client.* / ui.* / error.*</code> —— 页面侧异常、下载与刷新等操作，以及服务端错误。
          </li>
        </ul>
        <p className="akw-logs__hint">
          下载文件是 JSONL（每行一条记录），第一行是环境信息（插件版本、DSH_HOME、Node/系统）。
          日志只在本机，不会自动上传。
        </p>
      </section>

      {!loading && summary !== null && summary.events === 0 ? (
        <div className="akw-empty">
          <div className="akw-empty__title">还没有记录</div>
          <div className="akw-empty__desc">先用一次「配置工具」或让 agent 调一次工具，再回来刷新即可看到轨迹。</div>
        </div>
      ) : null}
    </div>
  )
}
