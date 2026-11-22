/**
 * 对象分析产物落盘（host 侧）。
 *
 * 页面和 agent 的「一次分析」都必须在这里留痕：
 *   <workspace>/akso-workbench-data/analysis/<对象名>-<时间戳>/
 *     - <code>-analysis-<日期>.md
 *     - <code>-analysis-<日期>.drawio
 *     - <code>-analysis-<日期>.png
 *
 * PNG 由 SVG 预览用本机 Chrome/Edge headless 截图生成；生成失败不阻断分析，
 * 但会把原因放进 warnings，调用方和页面都能看到「产物不完整」。
 */
import { execFile } from 'node:child_process'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { promisify } from 'node:util'

import type { AnalysisResult } from './analysis.ts'
import {
  analysisArtifactName,
  analysisOutputFolder,
  buildAnalysisDrawio,
  buildAnalysisMarkdown,
  buildAnalysisSvg,
} from './shared/analysis-artifacts.ts'

const runFile = promisify(execFile)

export interface AnalysisArtifacts {
  /** 绝对目录；给日志/工具返回值用。 */
  folder: string
  /** 相对 folder 的文件名。 */
  files: string[]
  warnings: string[]
}

const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
].filter((value): value is string => typeof value === 'string' && value !== '')

function findChromium(): string | null {
  for (const candidate of CHROME_CANDIDATES) {
    if (existsSync(candidate)) return candidate
  }
  return null
}

/** 把 SVG 预览渲染成 PNG；失败返回 null，不抛出。 */
async function renderPng(svg: string, target: string): Promise<string | null> {
  const browser = findChromium()
  if (browser === null) return '本机没有找到 Chrome/Edge，未生成 PNG'
  let work: string | null = null
  try {
    work = await mkdtemp(join(tmpdir(), 'akso-analysis-'))
    const html = `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#fff}svg{display:block}</style></head><body>${svg}</body></html>`
    const htmlPath = join(work, 'preview.html')
    await writeFile(htmlPath, html, 'utf8')
    const heightMatch = /<svg[^>]*\bheight="(\d+)"/i.exec(svg)
    const height = heightMatch === null ? 1600 : Number(heightMatch[1])
    const url = `file:///${htmlPath.replaceAll('\\', '/')}`
    await runFile(browser, [
      '--headless=new',
      '--disable-gpu',
      '--hide-scrollbars',
      '--no-first-run',
      '--no-default-browser-check',
      '--allow-file-access-from-files',
      `--window-size=1180,${height}`,
      `--screenshot=${target}`,
      url,
    ], { timeout: 30_000, windowsHide: true })
    const bytes = await readFile(target)
    return bytes.length > 0 ? null : 'Chrome 没有写出 PNG'
  } catch (error) {
    return `PNG 渲染失败：${error instanceof Error ? error.message : String(error)}`
  } finally {
    if (work !== null) await rm(work, { recursive: true, force: true }).catch(() => undefined)
  }
}

export async function writeAnalysisArtifacts(
  result: AnalysisResult,
  workspaceRoot: string,
  generatedAt: Date = new Date(),
): Promise<AnalysisArtifacts> {
  const relativeFolder = analysisOutputFolder(result, generatedAt)
  const folder = join(workspaceRoot, ...relativeFolder.split('/'))
  await mkdir(folder, { recursive: true })

  const warnings: string[] = []
  const markdownName = analysisArtifactName(result, 'md', generatedAt)
  const drawioName = analysisArtifactName(result, 'drawio', generatedAt)
  const pngName = analysisArtifactName(result, 'png', generatedAt)

  await writeFile(join(folder, markdownName), buildAnalysisMarkdown(result, { generatedAt }), 'utf8')
  await writeFile(join(folder, drawioName), buildAnalysisDrawio(result, { generatedAt }), 'utf8')

  const files = [markdownName, drawioName]
  if (process.env.AKSO_WORKBENCH_SKIP_PNG !== '1') {
    const pngWarning = await renderPng(buildAnalysisSvg(result, { generatedAt }), join(folder, pngName))
    if (pngWarning === null) files.push(pngName)
    else warnings.push(pngWarning)
  }
  return { folder, files, warnings }
}
