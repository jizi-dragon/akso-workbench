/**
 * 检查更新 —— 查正式版、下载安装包、说清怎么装（2026-11-27）
 *
 * ## 「有新版本」的判据是 MINOR 变大，不是 PATCH
 *
 * 仓库的版本口径（AGENTS.md / VERSIONING.md）：日常提交 PATCH +1，**打 tag / 发 release
 * 才 MINOR +1**。所以 `0.2.28 → 0.2.29` 只是又提交了一次，`0.2.28 → 0.3.0` 才是"出了一个正式版"。
 * 这里因此给两个结论：
 *   · `hasUpdate`  —— 正式版更新（major 或 minor 变大），要提醒；
 *   · `patchAhead` —— 同一条版本线上更高的小版本，只做提示，不催。
 *
 * ## 版本从哪儿来
 *
 * 只有一个来源：akso-cloud 下载页的 `workbench/latest.json`（我们自己发的正式清单），
 * 自测时可用环境变量 `AKSO_WORKBENCH_UPDATE_URL` 指到别处。
 *
 * 为什么**不**兜底到 GitHub Releases：这个仓库的 tag 里既有插件线（v0.2.x）也有早期的桌面安装器线
 * （v0.3.3 / v0.3.4 / v0.3.12 / v0.3.13 / v0.4.1），`releases/latest` 会返回后者，
 * 于是 0.2.28 的用户会被提示升到"0.4.1"——两个完全不同形态的东西。宁可不提示，也不给错提示。
 *
 * ## 为什么不在页面里直接替换插件文件
 *
 * DSH 的插件是**启动时加载**的 bundle：`plugin/lib/*.js` 被读进内存后，换文件不会换掉正在跑的代码，
 * 必须完全退出 DSH 再打开。dshmarket 的 update API 只覆盖 **npm 源**安装的包（我们是 `link:` 本地目录），
 * 而且它自己的成功结果里也带 `restartRequired`。所以这里做的是：
 *   查版本 → 下载 zip 到 `$DSH_HOME/akso-workbench/updates/<版本>/` → 校验 sha256 →
 *   按当前安装方式给替换步骤。**不碰正在运行的插件目录**，也不假装能热更新。
 */
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { pluginVersion } from './version.ts'

/** akso-cloud 下载页的正式清单（部署后公开可读）。 */
export const DEFAULT_FEED_URL = 'https://www.dragonrain.top/downloads/workbench/latest.json'

const FEED_FORMAT = 'akso-workbench-latest'
const CACHE_MS = 10 * 60 * 1000
const FEED_TIMEOUT_MS = 8000
const DOWNLOAD_TIMEOUT_MS = 120_000
const MAX_ZIP_BYTES = 64 * 1024 * 1024

/* ────────────────────────────── 版本号 ────────────────────────────── */

export interface Semver {
  major: number
  minor: number
  patch: number
}

/** 解析 `1.2.3` / `v1.2.3` / `1.2.3-beta.1`（预发布后缀忽略）；不合法返回 null。 */
export function parseSemver(value: unknown): Semver | null {
  if (typeof value !== 'string') return null
  const matched = /^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/.exec(value.trim())
  if (matched === null) return null
  return { major: Number(matched[1]), minor: Number(matched[2]), patch: Number(matched[3]) }
}

/** `a` 比 `b` 新返回 1，旧返回 -1，相同返回 0。 */
export function compareSemver(a: Semver, b: Semver): number {
  if (a.major !== b.major) return a.major > b.major ? 1 : -1
  if (a.minor !== b.minor) return a.minor > b.minor ? 1 : -1
  if (a.patch !== b.patch) return a.patch > b.patch ? 1 : -1
  return 0
}

/**
 * 是否是"正式版更新"：major 或 minor 变大。
 * 只比 patch 变大不算 —— 那是日常提交，用户不需要为它换一次插件。
 */
export function isOfficialUpgrade(current: string, latest: string): boolean {
  const a = parseSemver(current)
  const b = parseSemver(latest)
  if (a === null || b === null) return false
  return b.major > a.major || (b.major === a.major && b.minor > a.minor)
}

/** 同一条版本线上更高的 patch（`0.2.28 → 0.2.29`）：提示，但不催。 */
export function isPatchAhead(current: string, latest: string): boolean {
  const a = parseSemver(current)
  const b = parseSemver(latest)
  if (a === null || b === null) return false
  return a.major === b.major && a.minor === b.minor && b.patch > a.patch
}

/* ────────────────────────────── 清单 ────────────────────────────── */

export interface UpdateFeed {
  version: string
  tag: string | null
  name: string | null
  releaseUrl: string | null
  zipUrl: string | null
  zipSha256: string | null
  zipBytes: number | null
  publishedAt: string | null
  notes: string | null
}

export interface InstallHint {
  /** `link` = 本地目录安装（`link:` 依赖）；`version` = 按版本号安装；`unknown` = 读不到档案。 */
  mode: 'link' | 'version' | 'unknown'
  profile: string | null
  /** `link` 模式下当前被链接的目录（替换目标）。 */
  pluginDir: string | null
  steps: string[]
}

export interface UpdateCheckResult {
  ok: boolean
  current: string
  latest: string | null
  /** 正式版更新（minor/major 变大）——横幅只认这个。 */
  hasUpdate: boolean
  /** 同线更高 patch —— 只做提示。 */
  patchAhead: boolean
  source: 'feed' | 'none'
  feedUrl: string
  checkedAt: string
  releaseUrl: string | null
  zipUrl: string | null
  /** 清单里公布的包哈希；GitHub 兜底拿不到（下载后由用户核对发布页面）。 */
  zipSha256: string | null
  zipBytes: number | null
  publishedAt: string | null
  notes: string | null
  install: InstallHint
  error: string | null
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : null
}

function countOf(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : null
}

/** 只接受 https：更新包走明文 http 会让"校验 sha256"变成一句空话。 */
function httpsUrl(value: unknown): string | null {
  const raw = text(value)
  if (raw === null) return null
  try {
    const url = new URL(raw)
    return url.protocol === 'https:' ? url.toString() : null
  } catch {
    return null
  }
}

/** 我们自己发的清单：`{format:'akso-workbench-latest', version, zipUrl, zipSha256, …}`。 */
export function parseFeed(raw: unknown): UpdateFeed | null {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return null
  const body = raw as Record<string, unknown>
  const version = text(body.version)
  if (version === null || parseSemver(version) === null) return null
  // format 只是给"看起来像什么"用的：真清单必须有 version，格式名对不上多半是写错了文件
  const format = text(body.format)
  if (format !== null && format !== FEED_FORMAT) return null
  return {
    version,
    tag: text(body.tag),
    name: text(body.name),
    releaseUrl: httpsUrl(body.releaseUrl),
    zipUrl: httpsUrl(body.zipUrl),
    zipSha256: text(body.zipSha256)?.toLowerCase() ?? null,
    zipBytes: countOf(body.zipBytes),
    publishedAt: text(body.publishedAt),
    notes: text(body.notes),
  }
}

/* ────────────────────────────── 安装方式 ────────────────────────────── */

/** 插件目录：源码跑是 `plugin/src/`，打包后是 `plugin/lib/` —— 往上都是 `plugin/`。 */
export function pluginDir(): string {
  try {
    return join(dirname(fileURLToPath(import.meta.url)), '..')
  } catch {
    return ''
  }
}

/**
 * 从 profile 的 `package.json` 反推安装方式，给出对应的替换步骤。
 * 这是**唯一**能判断"我们被怎么装的"地方：`link:` 说明是本地目录（开发态），
 * 版本号说明是装进 profile 的 `node_modules`。
 */
export function installHint(env: NodeJS.ProcessEnv = process.env): InstallHint {
  const profile = text(env.DSH_PROFILE) ?? null
  const dir = text(env.DSH_PROFILE_DIR)
  const fallback: InstallHint = {
    mode: 'unknown',
    profile,
    pluginDir: pluginDir() || null,
    steps: [
      '完全退出 DSH（关窗口不算，要退进程）',
      '把下载包解压，用里面的 plugin 目录替换当前插件目录',
      '重新打开 DSH，到「插件」里确认版本号已变',
    ],
  }
  if (dir === null) return fallback
  const file = join(dir, 'package.json')
  if (!existsSync(file)) return fallback
  try {
    const pkg = JSON.parse(readFileSync(file, 'utf8')) as { dependencies?: Record<string, unknown> }
    const declared = pkg.dependencies?.['akso-workbench-dsh-ui']
    if (typeof declared !== 'string') return fallback
    if (declared.startsWith('link:')) {
      const target = declared.slice('link:'.length).replace(/\//g, '\\')
      return {
        mode: 'link',
        profile,
        pluginDir: target,
        steps: [
          '完全退出 DSH（关窗口不算，要退进程）',
          `解压下载包，用里面的 plugin 目录内容覆盖：${target}`,
          '重新打开 DSH，到「插件」里确认版本号已变',
        ],
      }
    }
    return {
      mode: 'version',
      profile,
      pluginDir: null,
      steps: [
        '完全退出 DSH（关窗口不算，要退进程）',
        `执行：dsh plugin --profile ${profile ?? '<你的 profile>'} add "<下载包解压后的 plugin 目录>"`,
        '重新打开 DSH，到「插件」里确认版本号已变',
      ],
    }
  } catch {
    return fallback
  }
}

/* ────────────────────────────── 网络 ────────────────────────────── */

type FetchLike = typeof fetch

function errorText(error: unknown): string {
  if (error instanceof Error) {
    // AbortSignal.timeout 抛的是 DOMException(TimeoutError)，消息是英文的 "The operation was aborted"
    if (error.name === 'TimeoutError' || error.name === 'AbortError') return '请求超时'
    return error.message
  }
  return String(error)
}

async function fetchJson(url: string, timeoutMs: number, fetchImpl: FetchLike): Promise<unknown> {
  const response = await fetchImpl(url, {
    signal: AbortSignal.timeout(timeoutMs),
    headers: { Accept: 'application/json', 'User-Agent': `akso-workbench/${pluginVersion() || '0'}` },
    cache: 'no-store',
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return await response.json()
}

/* ────────────────────────────── 检查 ────────────────────────────── */

interface CacheEntry {
  feedUrl: string
  at: number
  result: UpdateCheckResult
}

let cache: CacheEntry | null = null

export interface CheckOptions {
  /** 覆盖清单地址（默认取环境变量，再退回 akso-cloud）；GitHub 兜底不受它影响。 */
  feedUrl?: string
  /** 跳过 10 分钟缓存。 */
  force?: boolean
  now?: number
  fetchImpl?: FetchLike
}

/**
 * 查一次更新。**永远不抛错**：拿不到就返回 `ok:false` 与逐条原因 ——
 * 这个接口在页面打开时被调用，抛错只会让"有没有更新"变成一片红。
 */
export async function checkForUpdate(options: CheckOptions = {}): Promise<UpdateCheckResult> {
  const current = pluginVersion()
  const feedUrl = text(options.feedUrl) ?? text(process.env.AKSO_WORKBENCH_UPDATE_URL) ?? DEFAULT_FEED_URL
  const now = options.now ?? Date.now()
  const fetchImpl = options.fetchImpl ?? fetch

  if (!options.force && cache !== null && cache.feedUrl === feedUrl && now - cache.at < CACHE_MS) {
    return cache.result
  }

  const errors: string[] = []
  let feed: UpdateFeed | null = null
  let source: UpdateCheckResult['source'] = 'none'

  try {
    const parsed = parseFeed(await fetchJson(feedUrl, FEED_TIMEOUT_MS, fetchImpl))
    if (parsed === null) errors.push(`清单格式不对：${feedUrl}`)
    else {
      feed = parsed
      source = 'feed'
    }
  } catch (error) {
    errors.push(`读取清单失败：${errorText(error)}（${feedUrl}）`)
  }

  const result: UpdateCheckResult = {
    ok: feed !== null,
    current,
    latest: feed?.version ?? null,
    hasUpdate: feed !== null && isOfficialUpgrade(current, feed.version),
    patchAhead: feed !== null && isPatchAhead(current, feed.version),
    source,
    feedUrl,
    checkedAt: new Date(now).toISOString(),
    releaseUrl: feed?.releaseUrl ?? null,
    zipUrl: feed?.zipUrl ?? null,
    zipSha256: feed?.zipSha256 ?? null,
    zipBytes: feed?.zipBytes ?? null,
    publishedAt: feed?.publishedAt ?? null,
    notes: feed?.notes ?? null,
    install: installHint(),
    error: errors.length === 0 ? null : errors.join('；'),
  }
  if (feed !== null) cache = { feedUrl, at: now, result }
  return result
}

/** 只给测试用：清掉进程内缓存，避免用例之间互相影响。 */
export function resetUpdateCache(): void {
  cache = null
}

/* ────────────────────────────── 下载 ────────────────────────────── */

export interface DownloadResult {
  ok: boolean
  version: string
  path: string
  bytes: number
  sha256: string
  steps: string[]
}

export interface DownloadOptions {
  dshHome: string
  fetchImpl?: FetchLike
  check?: UpdateCheckResult
}

function safeName(value: string): string {
  return basename(value).replace(/[^\w.-]+/g, '_')
}

/**
 * 把更新包下载到 `$DSH_HOME/akso-workbench/updates/<版本>/` 并校验 sha256。
 *
 * 三条边界：
 *   · 只接受 https（清单解析时已经过滤过一次，这里再挡一次）；
 *   · 超过 64 MB 或声明大小对不上就中止（不写半截文件）；
 *   · 哈希不符直接删掉并报错 —— 校验失败的文件留在磁盘上只会被误用。
 */
export async function downloadUpdate(options: DownloadOptions): Promise<DownloadResult> {
  const check = options.check ?? (await checkForUpdate({ force: true }))
  const fetchImpl = options.fetchImpl ?? fetch
  if (check.latest === null || check.zipUrl === null) throw new Error('没有可下载的更新包（先检查更新）')
  if (!check.hasUpdate && !check.patchAhead) throw new Error(`当前 ${check.current} 已是最新，无需下载`)
  const url = httpsUrl(check.zipUrl)
  if (url === null) throw new Error('更新包地址不是 https，已拒绝下载')

  const response = await fetchImpl(url, { signal: AbortSignal.timeout(DOWNLOAD_TIMEOUT_MS), redirect: 'follow' })
  if (!response.ok) throw new Error(`下载失败：HTTP ${response.status}`)
  const declared = countOf(check.zipBytes)
  const length = Number(response.headers.get('content-length') ?? '0') || 0
  if (length > MAX_ZIP_BYTES) throw new Error(`更新包过大（${length} 字节），已拒绝下载`)
  const buffer = Buffer.from(await response.arrayBuffer())
  if (buffer.byteLength === 0) throw new Error('下载到 0 字节，已中止')
  if (buffer.byteLength > MAX_ZIP_BYTES) throw new Error(`更新包过大（${buffer.byteLength} 字节），已中止`)
  if (declared !== null && buffer.byteLength !== declared) {
    throw new Error(`大小对不上：清单写 ${declared} 字节，实际 ${buffer.byteLength} 字节`)
  }
  const sha256 = createHash('sha256').update(buffer).digest('hex')
  const expected = check.zipSha256?.toLowerCase() ?? null
  if (expected !== null && expected !== sha256) {
    throw new Error(`sha256 校验失败（清单 ${expected.slice(0, 12)}…，实际 ${sha256.slice(0, 12)}…），已丢弃`)
  }

  const dir = join(options.dshHome, 'akso-workbench', 'updates', check.latest)
  mkdirSync(dir, { recursive: true })
  const name = safeName(decodeURIComponent(new URL(url).pathname)) || `akso-workbench-${check.latest}.zip`
  const path = join(dir, name)
  writeFileSync(path, buffer)
  if (!existsSync(path) || statSync(path).size !== buffer.byteLength) throw new Error(`写入后回读大小不符：${path}`)
  return { ok: true, version: check.latest, path, bytes: buffer.byteLength, sha256, steps: check.install.steps }
}
