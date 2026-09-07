/** 后端 API 封装 + SSE 流解析 */

async function jsonFetch(url, options) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let message = `请求失败（${res.status}）`;
    try {
      const data = await res.json();
      if (data.detail) message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch { /* ignore */ }
    throw new Error(message);
  }
  return res.json();
}

export const api = {
  bootstrap: () => jsonFetch("/api/bootstrap"),
  listConversations: () => jsonFetch("/api/conversations"),
  createConversation: () => jsonFetch("/api/conversations", { method: "POST" }),
  deleteConversation: (id) => jsonFetch(`/api/conversations/${id}`, { method: "DELETE" }),
  listMessages: (id) => jsonFetch(`/api/conversations/${id}/messages`),
  saveSettings: (body) => jsonFetch("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  saveEmbedding: (body) => jsonFetch("/api/settings/embedding", { method: "PUT", body: JSON.stringify(body) }),
  testEmbedding: () => jsonFetch("/api/settings/test-embedding", { method: "POST" }),
  knowledge: () => jsonFetch("/api/knowledge"),
  uploadDocument: (name, content) =>
    jsonFetch("/api/knowledge/documents", { method: "POST", body: JSON.stringify({ name, content }) }),
  getDocument: (id) => jsonFetch(`/api/knowledge/documents/${id}`),
  deleteDocument: (id) => jsonFetch(`/api/knowledge/documents/${id}`, { method: "DELETE" }),
  reindex: () => jsonFetch("/api/knowledge/reindex", { method: "POST" }),
  search: (query) => jsonFetch("/api/search", { method: "POST", body: JSON.stringify({ query }) }),
  feedback: (metricId, helpful) =>
    jsonFetch("/api/feedback", { method: "POST", body: JSON.stringify({ metricId, helpful }) }),

  /** SSE 流式对话：onEvent(event) 回调每个事件 */
  async streamChat({ conversationId, message }, onEvent) {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversationId, message }),
    });
    if (!res.ok || !res.body) {
      throw new Error(`对话请求失败（${res.status}）`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const payload = trimmed.slice(5).trim();
        if (!payload) continue;
        try {
          onEvent(JSON.parse(payload));
        } catch { /* 跳过坏帧 */ }
      }
    }
  },
};
