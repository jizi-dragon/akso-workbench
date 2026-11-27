/*
 * capture 专用最小翻译扩展 —— content
 *
 * 行为（故意只保留这一条链路）：
 *   输入框/文本域/可编辑区获得焦点 → Ctrl+X
 *   → 取选中文本（没有选中就取整个输入）
 *   → 交给 background 翻译
 *   → 只写剪贴板，不改原输入
 *   → 用户自己 Ctrl+V 粘贴
 *
 * 不弹面板、不显示账号、不统计、不换源。
 */

const GUARD = '__akso_capture_translate_installed__'
if (window[GUARD]) {
  // 同一 frame 被重复注入时直接退出。
} else {
  window[GUARD] = true

  const TOAST_ID = 'akso-capture-translate-toast'
  let activeEditable = null
  let toastTimer = 0

  function editableOf(node) {
    if (!(node instanceof Element)) return null
    const el = node.closest(
      'input, textarea, [contenteditable=""], [contenteditable="true"], [contenteditable="plaintext-only"]',
    )
    if (!el) return null

    if (el instanceof HTMLInputElement) {
      const type = (el.type || 'text').toLowerCase()
      if (!['text', 'search', 'email', 'url', 'tel', 'number'].includes(type)) return null
      if (el.disabled || el.readOnly) return null
      return el
    }
    if (el instanceof HTMLTextAreaElement) {
      if (el.disabled || el.readOnly) return null
      return el
    }
    if (el instanceof HTMLElement && el.isContentEditable) return el
    return null
  }

  function textOf(el) {
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
      const start = el.selectionStart
      const end = el.selectionEnd
      if (typeof start === 'number' && typeof end === 'number' && end > start) {
        return el.value.slice(start, end)
      }
      return el.value
    }

    const selection = window.getSelection()
    if (selection && selection.rangeCount > 0) {
      const range = selection.getRangeAt(0)
      if (el.contains(range.commonAncestorContainer)) {
        const selected = selection.toString()
        if (selected.trim()) return selected
      }
    }
    return el.innerText
  }

  function ensureToast() {
    let host = document.getElementById(TOAST_ID)
    if (host) return host.shadowRoot

    host = document.createElement('div')
    host.id = TOAST_ID
    host.style.cssText = [
      'position:fixed',
      'right:16px',
      'bottom:16px',
      'z-index:2147483647',
      'pointer-events:none',
      'font:13px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif',
    ].join(';')
    const shadow = host.attachShadow({ mode: 'open' })
    shadow.innerHTML = [
      '<style>',
      '.toast{max-width:min(460px,calc(100vw - 32px));padding:9px 13px;border-radius:8px;',
      'background:#111827;color:#f9fafb;box-shadow:0 8px 24px rgba(0,0,0,.28);',
      'white-space:pre-wrap;word-break:break-word;opacity:0;transform:translateY(6px);',
      'transition:opacity .16s ease,transform .16s ease}',
      '.toast.show{opacity:1;transform:translateY(0)}',
      '.toast.success{background:#065f46}',
      '.toast.warning{background:#92400e}',
      '.toast.error{background:#991b1b}',
      '</style>',
      '<div class="toast" role="status"></div>',
    ].join('')
    document.documentElement.appendChild(host)
    return shadow
  }

  function showToast(message, kind = '', timeout = 2600) {
    const shadow = ensureToast()
    const toast = shadow.querySelector('.toast')
    if (!toast) return

    toast.textContent = message
    toast.className = `toast show ${kind}`.trim()
    window.clearTimeout(toastTimer)
    if (timeout > 0) {
      toastTimer = window.setTimeout(() => {
        toast.className = 'toast'
      }, timeout)
    }
  }

  async function writeClipboard(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text)
        return true
      }
    } catch {
      // 继续走 execCommand 兜底。
    }

    try {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.setAttribute('readonly', '')
      textarea.style.cssText = 'position:fixed;top:-1000px;left:-1000px;opacity:0'
      document.documentElement.appendChild(textarea)
      textarea.select()
      const ok = document.execCommand('copy')
      textarea.remove()
      return ok
    } catch {
      return false
    }
  }

  function requestTranslation(text) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type: 'akso-translate', text }, (reply) => {
          const lastError = chrome.runtime.lastError
          if (lastError) {
            resolve({ ok: false, error: lastError.message || '扩展通信失败' })
            return
          }
          resolve(reply || { ok: false, error: '翻译扩展没有返回结果' })
        })
      } catch (error) {
        resolve({ ok: false, error: error instanceof Error ? error.message : String(error) })
      }
    })
  }

  /**
   * 成功提示里带一小段译文，演示时不用先粘贴就能读到结果。
   * 折成一行、超过 160 字截断（剪贴板里仍是完整译文）。
   */
  function previewOf(text, max = 160) {
    const oneLine = String(text || '')
      .replace(/\s+/g, ' ')
      .trim()
    if (oneLine.length <= max) return oneLine
    return `${oneLine.slice(0, max - 1)}…`
  }

  async function translateFocused(el) {
    const text = textOf(el).trim()
    if (!text) {
      showToast('没有可翻译的文本', 'warning')
      return
    }

    showToast('翻译中…', '', 0)
    const reply = await requestTranslation(text)
    if (!reply.ok) {
      showToast(`❌ ${reply.error || '翻译失败'}`, 'error', 4200)
      return
    }

    const copied = await writeClipboard(reply.text)
    const meta = [reply.direction, reply.engine].filter(Boolean).join(' · ')
    const preview = previewOf(reply.text)
    if (copied) {
      const head = `✅ 已复制译文${meta ? `（${meta}）` : ''}`
      showToast(`${head}\n${preview}\nCtrl+V 粘贴`, 'success', 5200)
    } else {
      showToast(`⚠️ 翻译完成，但复制失败；请检查浏览器剪贴板权限\n${preview}`, 'warning', 5200)
    }
  }

  document.addEventListener(
    'focusin',
    (event) => {
      const el = editableOf(event.target)
      if (el) activeEditable = el
    },
    true,
  )

  document.addEventListener(
    'focusout',
    (event) => {
      if (activeEditable && event.target === activeEditable) activeEditable = null
    },
    true,
  )

  window.addEventListener(
    'keydown',
    (event) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return
      if (event.key.toLowerCase() !== 'x' || event.isComposing) return

      const el = editableOf(event.target) || activeEditable
      if (!el) return

      event.preventDefault()
      event.stopPropagation()
      void translateFocused(el)
    },
    true,
  )
}
