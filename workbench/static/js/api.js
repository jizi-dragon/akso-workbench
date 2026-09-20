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
    // 带上状态码：确认门要靠 409 区分「已决策/已过期」与真实故障，
    // 只拿到一句中文 detail 的调用方只能把它当普通错误弹出来
    const error = new Error(message);
    error.status = res.status;
    throw error;
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
  feedback: (metricId, helpful) =>
    jsonFetch("/api/feedback", { method: "POST", body: JSON.stringify({ metricId, helpful }) }),

  /** 系统提示词：读当前生效值 + 基线 + 是否自定义 */
  getPrompt: () => jsonFetch("/api/settings/prompt"),
  /** 保存提示词（传空串 = 恢复基线） */
  savePrompt: (prompt) =>
    jsonFetch("/api/settings/prompt", { method: "PUT", body: JSON.stringify({ prompt }) }),
  /** 缓存与用量概览（含命中率；命中价是未命中价的 1/50） */
  getCache: (days = 7) => jsonFetch(`/api/agent/cache?days=${days}`),

  /** 工具目录：插件 → 工具（含风险分级）与装配失败清单 */
  listTools: () => jsonFetch("/api/agent/tools"),
  /** 放行危险操作：返回一次性 approvalToken，由调用方原样带回重投那次调用 */
  approvePending: (pendingId) =>
    jsonFetch(`/api/agent/pending/${encodeURIComponent(pendingId)}/approve`, { method: "POST" }),
  rejectPending: (pendingId, reason) =>
    jsonFetch(`/api/agent/pending/${encodeURIComponent(pendingId)}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason: reason ?? "" }),
    }),
  /** 待确认清单：页面加载时用它认领**遗留在服务端**的待确认
   *  （只活在 SSE 流里的东西，刷新一下就没人管了——而那是一个真实写入的闸门） */
  listPending: (status = "pending") =>
    jsonFetch(`/api/agent/pending?status=${encodeURIComponent(status)}`),

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
