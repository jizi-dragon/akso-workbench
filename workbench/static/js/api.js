/** 后端 API 封装 + SSE 流解析 */

async function jsonFetch(url, options) {
  // ★ 对象 body 必须在这里统一序列化（2026-10-19 修）。
  //
  // 原实现只加 Content-Type、原样把 options 交给 fetch。fetch 对**普通对象** body
  // 会走 `String(obj)` ⇒ 实发 `"[object Object]"`。`/api/chat/stop` 因此长期 422，
  // 而调用方 catch 后把按钮退回"停止"，用户看到的是"点了没反应"（PITFALLS #122）。
  //
  // 放在**唯一入口**而不是逐个调用点补 `JSON.stringify`：后者是"每个新调用点都要记得"
  // 的约定，而漏一处不会报错、只会静默发错 body。字符串 body（调用点已序列化的）
  // 保持原样，避免二次编码。
  const opts = options ? { ...options } : {};
  if (opts.body != null && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
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
  /** 「本次运行」时间线（计划 §6.2）：run → 每步 span（模型/工具/确认）。
   *  `id` 为空表示不过滤会话（运维视角），传会话 id 才是"本次"。 */
  getRuns: (id = "", limit = 20) =>
    jsonFetch(`/api/agent/runs?conversationId=${encodeURIComponent(id)}&limit=${limit}`),
  saveSettings: (body) => jsonFetch("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  feedback: (metricId, helpful) =>
    jsonFetch("/api/feedback", { method: "POST", body: JSON.stringify({ metricId, helpful }) }),

  /** 系统提示词：读当前生效值 + 基线 + 是否自定义 */
  getPrompt: () => jsonFetch("/api/settings/prompt"),
  getToolScope: () => jsonFetch("/api/settings/tools"),
  listSchedules: () => jsonFetch("/api/schedules"),
  listGoals: () => jsonFetch("/api/goals"),
  createGoal: (body) =>
    jsonFetch("/api/goals", { method: "POST", body: JSON.stringify(body) }),
  patchGoal: (id, body) =>
    jsonFetch(`/api/goals/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteGoal: (id) =>
    jsonFetch(`/api/goals/${encodeURIComponent(id)}`, { method: "DELETE" }),
  createSchedule: (body) =>
    jsonFetch("/api/schedules", { method: "POST", body: JSON.stringify(body) }),
  patchSchedule: (id, body) =>
    jsonFetch(`/api/schedules/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteSchedule: (id) =>
    jsonFetch(`/api/schedules/${encodeURIComponent(id)}`, { method: "DELETE" }),
  runSchedule: (id) =>
    jsonFetch(`/api/schedules/${encodeURIComponent(id)}/run`, { method: "POST" }),
  saveToolScope: (allowed) =>
    jsonFetch("/api/settings/tools", { method: "PUT", body: JSON.stringify({ allowed }) }),
  /** 保存提示词（传空串 = 恢复基线） */
  savePrompt: (prompt) =>
    jsonFetch("/api/settings/prompt", { method: "PUT", body: JSON.stringify({ prompt }) }),
  /** 缓存与用量概览（含命中率；命中价是未命中价的 1/50） */
  getCache: (days = 7, conversationId = "") =>
    jsonFetch(`/api/agent/cache?days=${days}`
      + (conversationId ? `&conversationId=${encodeURIComponent(conversationId)}` : "")),

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
  /** MCP 服务器状态（纯内存，不打网络、不启动进程） */
  getMcpServers: () => jsonFetch("/api/mcp/servers"),
  /** 重读 mcp.json 并重建 MCP 连接（可能要几秒：启动 server 是真的起子进程） */
  reloadMcp: () => jsonFetch("/api/mcp/reload", { method: "POST" }),
  /** 只重连某一个 server（手动，不走退避） */
  reconnectMcp: (name) => jsonFetch(`/api/mcp/servers/${encodeURIComponent(name)}/reconnect`, { method: "POST" }),

  /** 云端账号库：登录状态 / 设备流 / 只读数据视图 */
  getVaultStatus: () => jsonFetch("/api/vault/status"),
  vaultLoginStart: (clientName = "桌面工作台") =>
    jsonFetch("/api/vault/login/start", { method: "POST", body: { clientName } }),
  vaultLoginPoll: (deviceCode) =>
    jsonFetch("/api/vault/login/poll", { method: "POST", body: { deviceCode } }),
  vaultLogout: () => jsonFetch("/api/vault/logout", { method: "POST" }),
  vaultPush: () => jsonFetch("/api/vault/push", { method: "POST" }),
  getVaultLibrary: (refresh = false) =>
    jsonFetch(`/api/vault/library${refresh ? "?refresh=1" : ""}`),
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

  /** 请求停止某个会话正在进行的一轮（计划 §5.6）。
   *
   *  ⚠️ **它是"请求"，不是"已经停了"**：服务端只是置一个位，循环会在下一个检查点
   *  （模型调用前 / 工具调用前 / 等待确认期间）收尾。所以调用方**不该**在这里就把界面
   *  切成"已停止" —— 真正的确认是 SSE 流里的 `cancelled` 事件。
   *  这也正是本仓不用 `AbortController` 做取消通道的原因：断连式取消留下半截状态
   *  （§5.4c 的成因），而这里的 `running: false` 只是"没在跑"的如实回报。
   */
  stopChat: (conversationId) =>
    jsonFetch("/api/chat/stop", { method: "POST", body: { conversationId } }),

  /** SSE 流式对话：onEvent(event) 回调每个事件 */
  async streamChat({ conversationId, message, attachmentIds = [], plan = false }, onEvent) {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversationId, message, attachmentIds, plan }),
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
