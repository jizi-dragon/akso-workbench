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
 *
 * 另外这里是「检查更新」的落脚点（用户要求放运行日志区）：查有没有**正式版**更新
 * （MINOR 变大才算，PATCH 只提示），有就出横幅，并把更新包下载到本机 + 给出替换步骤。
 * 换插件文件必须完全退出 DSH —— 页面不碰运行中的目录，这是 update.ts 里定下的边界。
 */
import { useEffect, useState } from 'react'

import {
  api,
  downloadJournal,
  track,
  type JournalSummaryView,
  type UpdateCheckView,
  type UpdateDownloadView,
} from './api.ts'

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
  /* 检查更新：最近一次结果、正在查/正在下、已下载的包、操作提示 */
  const [update, setUpdate] = useState<UpdateCheckView | null>(null)
  const [checking, setChecking] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [downloaded, setDownloaded] = useState<UpdateDownloadView | null>(null)
  const [updateNote, setUpdateNote] = useState<string | null>(null)

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

  /* ── 检查更新：查（有缓存，10 分钟一次）→ 下载到本机 → 给替换步骤 ── */

  async function checkUpdate(force: boolean): Promise<void> {
    setChecking(true)
    setUpdateNote(null)
    try {
      const result = await api.updateCheck(force)
      setUpdate(result)
      track('ui.update.check', {
        current: result.current,
        latest: result.latest,
        hasUpdate: result.hasUpdate,
        patchAhead: result.patchAhead,
        source: result.source,
      })
    } catch (cause) {
      setUpdateNote(`检查更新失败：${cause instanceof Error ? cause.message : String(cause)}`)
    } finally {
      setChecking(false)
    }
  }

  async function downloadUpdate(): Promise<void> {
    setDownloading(true)
    setUpdateNote(null)
    try {
      const result = await api.updateDownload()
      setDownloaded(result)
      track('ui.update.download', { version: result.version, bytes: result.bytes })
      setUpdateNote(`已下载 ${result.version}（${fmtBytes(result.bytes)}）到本机，按下面的步骤替换后重启 DSH`)
    } catch (cause) {
      setUpdateNote(`下载失败：${cause instanceof Error ? cause.message : String(cause)}`)
    } finally {
      setDownloading(false)
    }
  }

  async function copySteps(steps: string[]): Promise<void> {
    try {
      await navigator.clipboard.writeText(steps.map((step, index) => `${index + 1}. ${step}`).join('\n'))
      setUpdateNote('安装步骤已复制，可以直接发给同事')
    } catch {
      setUpdateNote('复制失败，请手动选中下面每一步')
    }
  }

  useEffect(() => {
    void checkUpdate(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const hasOfficial = update !== null && update.hasUpdate
  const steps = downloaded?.steps ?? update?.install.steps ?? []
  const updateError = update?.error ?? null

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
          <span className="akw-update__chip" title="当前安装的插件版本">
            v{update === null ? '—' : update.current}
          </span>
          <button
            type="button"
            className="akw-btn akw-btn--sm"
            onClick={() => void checkUpdate(true)}
            disabled={checking}
            title="检查 akso-cloud 上是否有新的正式版（MINOR 变大才算正式版）"
          >
            {checking ? '检查中…' : '检查更新'}
          </button>
          <button type="button" className="akw-btn akw-btn--sm" onClick={() => void load()} disabled={loading}>
            {loading ? '刷新中…' : '刷新'}
          </button>
          <button type="button" className="akw-btn akw-btn--sm" onClick={() => void copyDir()} disabled={summary === null}>
            复制日志目录
          </button>
        </div>
      </div>

      {hasOfficial && update !== null ? (
        <div className="akw-update akw-update--new" role="status" aria-live="polite">
          <div className="akw-update__main">
            <b>
              有新版本 <span className="akw-update__ver">{update.latest}</span>
              （当前 {update.current}）
            </b>
            <span>
              这是正式版更新{update.publishedAt === null ? '' : `，发布于 ${fmtTime(update.publishedAt)}`}。
              更新包会下载到本机，替换插件目录后要<strong>完全退出 DSH 再打开</strong>——插件在启动时加载，刷新页面不会生效。
            </span>
            {update.notes === null ? null : <span className="akw-update__notes">{update.notes}</span>}
          </div>
          <div className="akw-update__actions">
            <button
              type="button"
              className="akw-btn akw-btn--primary akw-btn--sm"
              onClick={() => void downloadUpdate()}
              disabled={downloading || downloaded !== null || update.zipUrl === null}
              title={
                update.zipUrl === null
                  ? '本次发布没有附带安装包，点「打开发布页」自行下载'
                  : '下载到本机并校验 sha256'
              }
            >
              {downloading
                ? '下载中…'
                : downloaded !== null
                  ? '已下载'
                  : update.zipUrl === null
                    ? '无安装包'
                    : '下载更新包'}
            </button>
            {update.releaseUrl === null ? null : (
              <a className="akw-btn akw-btn--sm" href={update.releaseUrl} target="_blank" rel="noreferrer">
                打开发布页
              </a>
            )}
            <button type="button" className="akw-btn akw-btn--sm" onClick={() => void copySteps(steps)}>
              复制安装步骤
            </button>
          </div>
        </div>
      ) : null}

      {downloaded === null ? null : (
        <div className="akw-update akw-update--ready" role="status">
          <div className="akw-update__main">
            <b>更新包已下载到本机（{fmtBytes(downloaded.bytes)}）</b>
            <code className="akw-update__path">{downloaded.path}</code>
            <span>
              sha256 <code>{downloaded.sha256.slice(0, 16)}…</code> —— 与发布页面上公布的哈希对照一致再用。
            </span>
          </div>
          <ol className="akw-update__steps">
            {steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </div>
      )}

      {update !== null && update.patchAhead && !update.hasUpdate ? (
        <div className="akw-notice" role="status">
          <span className="akw-notice__grow">
            同一条版本线上已有更高的小版本 {update.latest}（当前 {update.current}）。这类日常提交不必换插件；
            出现新的正式版（MINOR 变大）时这里会变成提醒。
          </span>
        </div>
      ) : null}

      {updateError !== null ? (
        <div className="akw-notice" role="status">
          <span className="akw-notice__grow">检查更新没成功：{updateError}（不影响当前使用）</span>
        </div>
      ) : null}

      {updateNote !== null ? (
        <div className="akw-notice" role="status" aria-live="polite">
          <span className="akw-notice__grow">{updateNote}</span>
        </div>
      ) : null}

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
