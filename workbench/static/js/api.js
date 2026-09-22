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
  /** 放行危险操作：返回一次性 approvalToken，由调用方原样带回重投那次调用。
   *
   *  `value` 只对**选择型确认**（待确认里带 `options`）有意义：它是用户在卡片上点中的
   *  那一项。服务端会校验它确实在候选项里——编出来的值会被 409 拒掉，不会静默当参数用。
   */
  approvePending: (pendingId, value = "") =>
    jsonFetch(`/api/agent/pending/${encodeURIComponent(pendingId)}/approve`, {
      method: "POST",
      body: JSON.stringify({ value: value ?? "" }),
    }),
  rejectPending: (pendingId, reason) =>
    jsonFetch(`/api/agent/pending/${encodeURIComponent(pendingId)}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason: reason ?? "" }),
    }),
  /** 待确认清单：页面加载时用它认领**遗留在服务端**的待确认
   *  （只活在 SSE 流里的东西，刷新一下就没人管了——而那是一个真实写入的闸门） */
  listPending: (status = "pending") =>
    jsonFetch(`/api/agent/pending?status=${encodeURIComponent(status)}`),
  /** 这个会话当前选中的账号 / 环境（刷新后把右下角那张卡片画回来） */
  getSelection: (conversationId) =>
    jsonFetch(`/api/agent/selection?conversationId=${encodeURIComponent(conversationId || "")}`),
  /** **界面发起**一次选账号：服务端按账号表展开候选项并登记一条待确认。
   *  返回的形状与 `confirm_required` 事件里的 confirm 一致（pendingId / options / …），
   *  所以同一套卡片弹窗直接就能用。 */
  pickSelection: (conversationId, question = "") =>
    jsonFetch("/api/agent/selection/pick", {
      method: "POST",
      body: JSON.stringify({ conversationId: conversationId || "", question }),
    }),

  /** 上传一个附件（multipart）。**不要走 jsonFetch**：它写死了
   *  `Content-Type: application/json`，而 multipart 的 boundary 必须由浏览器自己生成。 */
  async uploadAttachment(file, conversationId = "") {
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("conversationId", conversationId || "");
    const res = await fetch("/api/attachments", { method: "POST", body: form });
    let payload = null;
    try { payload = await res.json(); } catch { /* 非 JSON 错误体 */ }
    if (!res.ok) {
      const detail = payload && payload.detail;
      const error = new Error(typeof detail === "string" ? detail : `上传失败（${res.status}）`);
      error.status = res.status;
      throw error;
    }
    return payload;
  },
  listAttachments: (conversationId = "") =>
    jsonFetch(`/api/attachments?conversationId=${encodeURIComponent(conversationId || "")}`),
  deleteAttachment: (id) =>
    jsonFetch(`/api/attachments/${encodeURIComponent(id)}`, { method: "DELETE" }),
  /** 原文件的 URL（图片在对话里直接 <img> 显示它） */
  attachmentRawUrl: (id) => `/api/attachments/${encodeURIComponent(id)}/raw`,

  /** SSE 流式对话：onEvent(event) 回调每个事件 */
  async streamChat({ conversationId, message, attachmentIds = [] }, onEvent) {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversationId, message, attachmentIds }),
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
