/*
 * capture 专用最小翻译扩展 —— background
 *
 * 只做一件事：把 content 发来的文本翻译成另一种语言。
 * 不读取账号、不采集页面、不写日志、不统计、不换源。
 *
 * 2026-11-27：与 akso-pass 4.6.6 的翻译链路同步。
 *   - 移掉两台已经不可用的引擎：TranSmart（服务停运，连不上，每次白等满 8s）、Lingva（Cloudflare 403）。
 *   - 只留两路，按顺序串行：有道词典（查词，20–50ms）→ MyMemory（翻整句，0.4–2.1s）。
 *   - 有道取的是"网络释义"，是**词典**：查不到、或返回 `xx / yy`、`sth/sb` 这种词典式结果时，
 *     判为不可用，交给 MyMemory；否则用户会得到 "take action /measures /steps to do sth" 这种垃圾。
 *   - 结果整形：短词/短语（单行、≤12 字、无句读、译文 ≤4 词）转 snake_case，可直接当字段名；
 *     整句/多行保持英文句子、首字母大写；目标是中文时原样返回。
 *
 * 方向由文本自动判断：
 *   - 含中文 → 中文译英文
 *   - 不含中文 → 英文译中文
 * 这样同一套快捷键既能写 eGMP 英文值，也能把英文界面翻回中文。
 */

const YOUDAO_TIMEOUT_MS = 3000
const MYMEMORY_TIMEOUT_MS = 8000
const MAX_TEXT_LENGTH = 4000
const SNAKE_MAX_LEN = 60
const SNAKE_MIN_LEN = 3

const CJK_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/
const MYMEMORY_LANG = { 'zh-CN': 'zh-CN', en: 'en-US' }

/** 这些词在字段名里本来就该整体保留（不拆成 g_m_p），转换时只做大小写归一。 */
const KEEP_AS_IS = new Set(['gmp', 'sop', 'capa', 'qa', 'qc', 'id', 'api', 'url', 'dms', 'akso', 'egmp'])

function directionOf(text) {
  return CJK_RE.test(text)
    ? { source: 'zh-CN', target: 'en', label: '中 → 英' }
    : { source: 'en', target: 'zh-CN', label: '英 → 中' }
}

function withTimeout(run, label, ms) {
  return new Promise((resolve, reject) => {
    const controller = new AbortController()
    const timer = setTimeout(() => {
      controller.abort()
      reject(new Error(`${label} 超时（${Math.round(ms / 1000)}s）`))
    }, ms)

    run(controller.signal).then(
      (value) => {
        clearTimeout(timer)
        resolve(value)
      },
      (error) => {
        clearTimeout(timer)
        reject(error)
      },
    )
  })
}

/* ────────────────────────── 1. 有道词典（查词，最快） ────────────────────────── */

/**
 * 有道取的是 web_trans「网络释义」首条 —— 这是**词典**而不是翻译器。
 * 它不接受语言参数，只服务中↔英；查不到就抛错，让 MyMemory 接手。
 * 两个"看起来成功其实没翻"的坑在这里挡住：未收录、结果与原文完全相同。
 */
async function viaYoudao(text, _direction, signal) {
  const url =
    `https://dict.youdao.com/jsonapi?q=${encodeURIComponent(text)}` +
    `&dicts=${encodeURIComponent('{"count":99,"dicts":[["web_trans"]]}')}`

  const response = await fetch(url, {
    method: 'GET',
    headers: { Referer: 'https://dict.youdao.com/' },
    signal,
  })
  if (!response.ok) throw new Error(`有道 HTTP ${response.status}`)

  const data = await response.json()
  const list = data && data.web_trans && data.web_trans['web-translation']
  if (!Array.isArray(list) || list.length === 0) {
    throw new Error('有道未收录该词')
  }

  const raw = list[0] && Array.isArray(list[0].trans) ? list[0].trans[0] && list[0].trans[0].value : ''
  // 该接口的 value 有时夹带 HTML（`<b>xx</b>`），必须剥掉
  const result = String(raw == null ? '' : raw)
    .replace(/<[^>]+>/g, '')
    .trim()

  if (!result) throw new Error('有道返回为空')
  if (result.toLowerCase() === text.trim().toLowerCase()) throw new Error('有道未提供有效翻译')
  return result
}

/* ────────────────────────── 2. MyMemory（翻整句） ────────────────────────── */

async function viaMyMemory(text, direction, signal) {
  const from = MYMEMORY_LANG[direction.source] || direction.source
  const to = MYMEMORY_LANG[direction.target] || direction.target
  const url =
    `https://api.mymemory.translated.net/get?q=${encodeURIComponent(text)}` +
    `&langpair=${encodeURIComponent(`${from}|${to}`)}`

  const response = await fetch(url, { signal })
  if (!response.ok) throw new Error(`MyMemory HTTP ${response.status}`)

  const data = await response.json()
  if (Number(data.responseStatus) !== 200) {
    throw new Error(`MyMemory：${data.responseDetails || data.responseStatus}`)
  }
  const result = data.responseData && data.responseData.translatedText
  if (!result) throw new Error('MyMemory 返回为空')
  // 该接口把"额度用尽"当正常返回塞进正文，不查这两个串就会把警告当译文复制走
  if (result.includes('MYMEMORY WARNING') || result.toUpperCase().includes('QUOTA')) {
    throw new Error('MyMemory 今日免费额度已用完')
  }
  return String(result)
}

/* ────────────────────────── 结果整形 ────────────────────────── */

/** 词典式结果（斜杠并列词条、sth/sb 占位符）判为不可用 —— 宁可重翻，也不给垃圾。 */
function looksLikeDictionaryEntry(text) {
  if (/ \/ |\/ /.test(text)) return true
  if (/\b(?:sth|sb)\b/i.test(text)) return true
  return false
}

/**
 * 转命名规范（snake_case）。规则顺序有意义：
 *   1) 含换行 ⇒ 原样返回（多行是段落）
 *   2) 括号内容拆成独立词；连字符/斜杠/点/空白 → 下划线；只留 a-z0-9_
 *   3) 超 60 按完整单词截断；短于 3 或为空 ⇒ 返回原文
 */
function toSnakeCase(text) {
  if (!text || typeof text !== 'string') return text
  const trimmed = text.trim()
  if (trimmed.includes('\n')) return trimmed

  let result = trimmed
    .toLowerCase()
    .replace(/\s*\(([^)]*)\)/g, (_m, inner) => `_${inner.trim()}`)
    .replace(/[-/.]/g, '_')
    .replace(/\s+/g, '_')
    .replace(/[^a-z0-9_]/g, '')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')

  result = result
    .split('_')
    .map((word) => (KEEP_AS_IS.has(word) ? word.toLowerCase() : word))
    .join('_')

  if (result.length > SNAKE_MAX_LEN) {
    const parts = result.split('_')
    let capped = ''
    for (const part of parts) {
      const candidate = capped ? `${capped}_${part}` : part
      if (candidate.length <= SNAKE_MAX_LEN) {
        capped = candidate
      } else {
        break
      }
    }
    result = capped || result.substring(0, SNAKE_MAX_LEN)
  }

  if (!result || result.length < SNAKE_MIN_LEN) return trimmed
  return result
}

/**
 * 目标语言是英语时的整形：
 *   - 短词/短语（单行、≤24 字、无句读）⇒ 转命名规范 snake_case，直接可当字段名（与 akso-pass 一致）
 *   - 整句/多行 ⇒ 保持句子形态，只把首字母大写（演示时读起来才是英文，而不是一串下划线）
 * 目标是中文 ⇒ 原样返回。
 */
const SENTENCE_MIN_LEN = 12
const TERM_MAX_WORDS = 4
const SENTENCE_PUNCT_RE = /[。！？；，,.;!?]/

function looksLikeSentence(text) {
  const trimmed = text.trim()
  return trimmed.includes('\n') || trimmed.length > SENTENCE_MIN_LEN || SENTENCE_PUNCT_RE.test(trimmed)
}

function sentenceCase(text) {
  const trimmed = text.trim()
  if (!trimmed) return trimmed
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1)
}

function normalize(result, direction, sourceText) {
  const text = result.trim()
  if (direction.target !== 'en') return text
  if (looksLikeSentence(sourceText)) return sentenceCase(text)
  const words = text.split(/\s+/).filter(Boolean)
  if (words.length > TERM_MAX_WORDS) return sentenceCase(text)
  return toSnakeCase(text)
}

/* ────────────────────────── 调度 ────────────────────────── */

const ENGINES = [
  { id: 'youdao', label: '有道', timeout: YOUDAO_TIMEOUT_MS, translate: viaYoudao },
  { id: 'mymemory', label: 'MyMemory', timeout: MYMEMORY_TIMEOUT_MS, translate: viaMyMemory },
]

async function translate(text) {
  const direction = directionOf(text)
  const errors = []

  for (const engine of ENGINES) {
    try {
      const raw = await withTimeout(
        (signal) => engine.translate(text, direction, signal),
        engine.label,
        engine.timeout,
      )
      const out = typeof raw === 'string' ? raw.trim() : ''
      if (!out) throw new Error('返回为空')
      if (out === text.trim()) throw new Error('结果与原文相同')
      if (looksLikeDictionaryEntry(out)) throw new Error('只给了词典释义，不是译文')
      if (direction.target === 'en' && CJK_RE.test(out)) throw new Error('结果仍是中文')

      return {
        ok: true,
        text: normalize(out, direction, text),
        engine: engine.label,
        direction: direction.label,
      }
    } catch (error) {
      errors.push(`${engine.label}：${error instanceof Error ? error.message : String(error)}`)
    }
  }

  return {
    ok: false,
    error: `翻译失败（${errors.join('；')}）`,
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== 'akso-translate') return false

  const text = typeof message.text === 'string' ? message.text.trim() : ''
  if (!text) {
    sendResponse({ ok: false, error: '没有可翻译的文本' })
    return false
  }
  if (text.length > MAX_TEXT_LENGTH) {
    sendResponse({ ok: false, error: `文本太长（最多 ${MAX_TEXT_LENGTH} 字）` })
    return false
  }

  translate(text).then(sendResponse).catch((error) => {
    sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) })
  })
  return true
})
