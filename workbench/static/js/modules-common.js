/* 模块页共享 JS 工具（SSE over POST / API 封装 / 简易 DOM 助手） */

export async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
    body: options.body != null && typeof options.body !== 'string'
      ? JSON.stringify(options.body) : options.body,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
  return data;
}

/** POST 版 SSE：fetch 流式读取 text/event-stream，逐帧回调。 */
export async function postSSE(path, body, { onLog, onDone, onError } = {}) {
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) {
    const data = await resp.json().catch(() => ({}));
    onError?.(new Error(data.detail || `HTTP ${resp.status}`));
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      for (const line of chunk.split('\n')) {
        if (!line.startsWith('data: ')) continue;
        try {
          const evt = JSON.parse(line.slice(6));
          if (evt.type === 'log') onLog?.(evt.line);
          else if (evt.type === 'done') onDone?.(evt);
        } catch { /* 非 JSON 帧，忽略 */ }
      }
    }
  }
}

export function el(id) { return document.getElementById(id); }

export function logLine(consoleEl, text, cls = '') {
  const div = document.createElement('div');
  if (cls) div.className = cls;
  div.textContent = text;
  consoleEl.appendChild(div);
  consoleEl.scrollTop = consoleEl.scrollHeight;
  while (consoleEl.children.length > 1200) consoleEl.removeChild(consoleEl.firstChild);
}

export function fmtBytes(n) {
  if (n == null) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

export function badgeFor(status) {
  const map = {
    ok: 'ok', succeeded: 'ok', online: 'ok', success: 'ok',
    running: 'warn', logging_in: 'warn', launching: 'warn', degraded: 'warn', timeout: 'warn',
    failed: 'err', error: 'err', missing: 'err', stopped: 'err', gave_up: 'err',
  };
  return map[status] || '';
}
