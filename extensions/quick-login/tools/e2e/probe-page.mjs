/**
 * 配置页结构探针（交互式调研用）—— node tools/e2e/probe-page.mjs
 *
 * 启动带 dist 扩展的隔离浏览器（档案 tmp/probe-profile，跨次保留登录态），
 * 持续采集所有页面/iframe 的：URL、document.title、面包屑/标题候选、iframe 列表，
 * 并经 CDP 抓取名称型 API 响应（guid→name 候选）。
 *
 * 落盘：
 *   tmp/probe/current.md     —— 最新页面快照（每次变化重写，主持方读取并复述）
 *   tmp/probe/api-log.jsonl  —— 名称型 API 响应候选（append-only）
 *   tmp/probe/history.jsonl  —— 页面切换历史（append-only）
 *
 * 主持方交互：读 current.md + api-log 尾部 → 向用户复述 → 用户切页 → 重复。
 */
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')), '..', '..');
const DIST = path.join(ROOT, 'dist');
const PROFILE = path.join(ROOT, 'tmp', 'probe-profile');
const CURRENT = path.join(ROOT, 'tmp', 'probe', 'current.md');
const API_LOG = path.join(ROOT, 'tmp', 'probe', 'api-log.jsonl');
const HISTORY = path.join(ROOT, 'tmp', 'probe', 'history.jsonl');

function findPwChromium() {
  const mp = path.join(os.homedir(), 'AppData', 'Local', 'ms-playwright');
  if (!fs.existsSync(mp)) return null;
  for (const d of fs.readdirSync(mp).filter((x) => /^chromium/i.test(x)).sort().reverse()) {
    for (const sub of ['chrome-win64', 'chrome-win']) {
      const p = path.join(mp, d, sub, 'chrome.exe');
      if (fs.existsSync(p)) return p;
    }
  }
  return null;
}
const EXECUTABLE = process.env.CHROME_PATH ?? findPwChromium();
if (!EXECUTABLE) throw new Error('未找到 Playwright Chromium');

fs.mkdirSync(path.dirname(CURRENT), { recursive: true });
const apiStream = fs.createWriteStream(API_LOG, { flags: 'a' });
const histStream = fs.createWriteStream(HISTORY, { flags: 'a' });

/** 名称型 API 白名单（路径片段） */
const NAME_API = /api\/platform\/.*(GetView|Detail|QueryList|GetUserMenuPermission|Workflow|Lifecycle|Template|GetObject|Menu|Form|Power|PageList|ByName|ById)/i;
/** 面包屑/标题候选选择器 */
const CRUMB_SEL = [
  '[class*="breadcrumb"]', '[class*="Breadcrumb"]',
  '[class*="page-title"]', '[class*="pageTitle"]', '[class*="header-title"]',
  '[class*="PageHeader"]', 'h1', 'h2',
].join(',');

function extractNames(json, out, depth = 0) {
  if (depth > 4 || out.length >= 24 || !json || typeof json !== 'object') return;
  if (Array.isArray(json)) {
    for (const it of json.slice(0, 30)) extractNames(it, out, depth + 1);
    return;
  }
  const name = json.name ?? json.Name ?? json.title ?? json.menuName ?? json.objectName;
  const id = json.id ?? json.guid ?? json.code ?? json.viewId ?? json.objectId;
  if (typeof name === 'string' && name.trim() && id !== undefined && id !== null && String(id).trim()) {
    out.push({ name: String(name).slice(0, 60), id: String(id).slice(0, 40) });
  }
  for (const v of Object.values(json)) {
    if (v && typeof v === 'object') extractNames(v, out, depth + 1);
  }
}

/** 对单个 frame 采集结构化快照 */
async function snapFrame(frame) {
  try {
    return await frame.evaluate((sel) => {
      const crumbs = [];
      for (const el of document.querySelectorAll(sel)) {
        const t = (el.textContent ?? '').replace(/\s+/g, ' ').trim();
        if (t && t.length <= 120 && !crumbs.includes(t)) crumbs.push(t);
        if (crumbs.length >= 8) break;
      }
      return { url: location.href, title: document.title, crumbs, ready: document.readyState };
    }, CRUMB_SEL);
  } catch {
    return null;
  }
}

let lastSig = '';

/** 全局登记簿：tabIdx → 最新快照；单一写入器合成全部页签（防互相覆盖） */
const latest = new Map();

function composeSnapshot() {
  const lines = [`# 探针快照 · ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`, ''];
  for (const m of [...latest.values()].sort((a, b) => a.tabIdx - b.tabIdx)) {
    if (!m.top) {
      lines.push(`## 页签 T${m.tabIdx} ⚠ 采样失败`);
      lines.push(...(m.errs ?? []).map((x) => `- ${x}`));
      lines.push('');
      continue;
    }
    lines.push(`## 页签 T${m.tabIdx} · 顶层 ${m.top.url}`);
    lines.push(`- 顶层标题: ${JSON.stringify(m.top.title)}`);
    if (m.top.crumbs.length) lines.push(`- 顶层候选: ${m.top.crumbs.map((c) => `「${c}」`).join(' ')}`);
    if (m.errs?.length) lines.push(`- ⚠ 采样警告: ${m.errs.join(' | ')}`);
    for (const f of m.frames) {
      if (f.url === m.top.url) continue;
      lines.push(`### iframe ${f.url.slice(0, 90)}`);
      lines.push(`  - 标题: ${JSON.stringify(f.title)}`);
      if (f.crumbs.length) lines.push(`  - 候选: ${f.crumbs.map((c) => `「${c}」`).join(' ')}`);
    }
    lines.push('');
  }
  const body = lines.join('\n');
  if (body !== lastSig) {
    lastSig = body;
    fs.writeFileSync(CURRENT, body, 'utf8');
    for (const m of latest.values()) {
      if (m.top) histStream.write(JSON.stringify({ t: Date.now(), tab: m.tabIdx, url: m.top.url, title: m.top.title }) + '\n');
    }
  }
}

async function watchPage(ctx, page, tabIdx) {
  const inflight = new Map();
  const cdp = await ctx.newCDPSession(page);
  await cdp.send('Network.enable').catch(() => undefined);

  cdp.on('Network.requestWillBeSent', (e) => {
    if (NAME_API.test(e.request.url)) inflight.set(e.requestId, e.request.url);
  });
  cdp.on('Network.loadingFinished', async (e) => {
    const url = inflight.get(e.requestId);
    inflight.delete(e.requestId);
    if (!url) return;
    try {
      const r = await cdp.send('Network.getResponseBody', { requestId: e.requestId });
      const body = r.base64Encoded ? Buffer.from(r.body, 'base64').toString('utf8') : r.body;
      if (!body || body.length > 2_000_000) return;
      let json = null;
      try { json = JSON.parse(body); } catch { return; }
      const names = [];
      extractNames(json, names);
      if (names.length) {
        apiStream.write(JSON.stringify({ t: Date.now(), url: url.slice(0, 220), names }) + '\n');
      }
    } catch {
      // body 不可得跳过
    }
  });

  const poll = async () => {
    if (page.isClosed()) {
      latest.delete(tabIdx);
      composeSnapshot();
      return;
    }
    const errs = [];
    let top = null;
    try {
      top = await snapFrame(page.mainFrame());
    } catch (e) {
      errs.push(`主框架: ${String(e?.message ?? e).slice(0, 100)}`);
    }
    if (!top) {
      errs.push('主框架采样为空（evaluate 失败或被销毁）');
    }
    const frames = [];
    for (const f of page.frames()) {
      if (f === page.mainFrame()) continue;
      try {
        const s = await snapFrame(f);
        if (s) frames.push(s);
      } catch (e) {
        errs.push(`iframe ${f.url().slice(0, 50)}: ${String(e?.message ?? e).slice(0, 80)}`);
      }
    }
    latest.set(tabIdx, { tabIdx, top, frames, errs });
    composeSnapshot();
  };
  page.on('load', () => void poll());
  page.on('framenavigated', () => void poll());
  setInterval(() => void poll(), 3000);
}

async function main() {
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    executablePath: EXECUTABLE,
    headless: false,
    viewport: null,
    args: [
      `--disable-extensions-except=${DIST}`,
      `--load-extension=${DIST}`,
      '--no-first-run', '--no-default-browser-check', '--start-maximized',
      '--hide-crash-restore-bubble', '--disable-sync', '--disable-features=MediaRouter',
    ],
  });
  console.log('✔ 探针浏览器已启动（档案 tmp/probe-profile）');
  let idx = 0;
  const tabIndex = new Map();
  for (const p of ctx.pages()) {
    tabIndex.set(p, ++idx);
    void watchPage(ctx, p, idx);
  }
  ctx.on('page', (p) => {
    tabIndex.set(p, ++idx);
    void watchPage(ctx, p, idx);
  });
  const ver = JSON.parse(fs.readFileSync(path.join(DIST, 'manifest.json'), 'utf8')).version;
  console.log(`✔ 扩展版本 ${ver} · 探针就绪：快照→ tmp/probe/current.md，API 候选→ tmp/probe/api-log.jsonl`);
  await new Promise(() => {});
}
main().catch((e) => {
  console.error('探针启动失败:', e);
  process.exit(1);
});
