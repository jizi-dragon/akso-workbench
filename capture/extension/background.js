/*
 * capture 专用最小翻译扩展 —— background
 *
 * 只做一件事：把 content 发来的文本翻译成另一种语言。
 * 不读取账号、不采集页面、不写日志、不统计、不换源。
 *
 * 方向由文本自动判断：
 *   - 含中文 → 中文译英文
 *   - 不含中文 → 英文译中文
 * 这样同一套快捷键既能写 eGMP 英文值，也能把英文界面翻回中文。
 */

const TRANSLATE_TIMEOUT_MS = 8000
const MAX_TEXT_LENGTH = 4000
const CJK_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/

const TRANSMART_LANG = { 'zh-CN': 'zh', en: 'en' }
const MYMEMORY_LANG = { 'zh-CN': 'zh-CN', en: 'en-US' }

function directionOf(text) {
  return CJK_RE.test(text)
    ? { source: 'zh-CN', target: 'en', label: '中 → 英' }
    : { source: 'en', target: 'zh-CN', label: '英 → 中' }
}

function withTimeout(run, label) {
  return new Promise((resolve, reject) => {
    const controller = new AbortController()
    const timer = setTimeout(() => {
      controller.abort()
      reject(new Error(`${label} 超时（${TRANSLATE_TIMEOUT_MS / 1000}s）`))
    }, TRANSLATE_TIMEOUT_MS)

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

async function viaTransmart(text, source, target, signal) {
  const clientKey =
    `browser-chrome-${(navigator.userAgent.match(/Chrome\/(\d+)/) || [])[1] || '120'}` +
    `-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

  const response = await fetch('https://transmart.qq.com/api/imt', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      header: { fn: 'auto_translation', session: '', client_key: clientKey, user: '' },
      type: 'plain',
      model_category: 'normal',
      text_domain: 'general',
      source: { lang: TRANSMART_LANG[source] || 'auto', text_list: [text] },
      target: { lang: TRANSMART_LANG[target] || 'en' },
    }),
    credentials: 'omit',
    signal,
  })

  if (!response.ok) throw new Error(`TranSmart HTTP ${response.status}`)
  const data = await response.json()
  if (data.header && data.header.ret_code && data.header.ret_code !== 'succ') {
    throw new Error(`TranSmart 返回错误：${data.header.ret_code}`)
  }
  if (!Array.isArray(data.auto_translation) || data.auto_translation.length === 0) {
    throw new Error('TranSmart 返回为空')
  }
  return data.auto_translation.map(String).join('\n')
}

async function viaMyMemory(text, source, target, signal) {
  const from = MYMEMORY_LANG[source] || source
  const to = MYMEMORY_LANG[target] || target
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
  if (result.includes('MYMEMORY WARNING') || result.toUpperCase().includes('QUOTA')) {
    throw new Error('MyMemory 免费额度用完')
  }
  return result
}

async function viaLingva(text, source, target, signal) {
  const code = (value) => (value === 'zh-CN' || value === 'zh-TW' ? 'zh' : value.split('-')[0])
  const url = `https://lingva.ml/api/v1/${code(source)}/${code(target)}/${encodeURIComponent(text)}`
  const response = await fetch(url, { signal })
  if (!response.ok) throw new Error(`Lingva HTTP ${response.status}`)
  const data = await response.json()
  if (!data.translation) throw new Error('Lingva 返回为空')
  return data.translation
}

const ENGINES = [
  { id: 'transmart', label: 'TranSmart', translate: viaTransmart },
  { id: 'mymemory', label: 'MyMemory', translate: viaMyMemory },
  { id: 'lingva', label: 'Lingva', translate: viaLingva },
]

async function translate(text) {
  const direction = directionOf(text)
  const errors = []

  for (const engine of ENGINES) {
    try {
      const result = await withTimeout(
        (signal) => engine.translate(text, direction.source, direction.target, signal),
        engine.label,
      )
      if (typeof result !== 'string' || result.trim() === '') {
        throw new Error('返回为空')
      }
      if (result.trim() === text.trim()) {
        throw new Error('结果与原文相同')
      }
      return {
        ok: true,
        text: result.trim(),
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
