/** 合规对话视图：消息渲染 + SSE 流式 + 审计轨迹 + 反馈 */

import { api } from "./api.js";
import { createAttachTray } from "./attachments.js";
import { renderMarkdown } from "./markdown.js";
import { createToolTrail, initToolPalette } from "./chat-tools.js";
import { initApprovalModal, recoverPending, requestDecision } from "./approval-modal.js";
import {
  PICK_ACCOUNT_EVENT,
  clearSelection,
  initAccountDock,
  restoreSelection,
  showDockError,
} from "./account-dock.js";
import { state } from "./app.js";

const messagesEl = () => document.getElementById("messages");
let isRunning = false;
let chatInitialized = false;
let pickingAccount = false;

/** 决定类事件 → 弹窗入参。
 *
 *  `confirm_required`（模型主动问）与 `approval_required`（宿主风险门）**都走弹窗**，
 *  但 `kind` 不同——两者的标题与说明文案不一样，因为对用户要做的动作不一样：
 *  前者点完模型自己继续，后者本轮已结束、要再让模型重试一次。
 */
function decisionFromConfirm(event) {
  return {
    pendingId: String(event.pendingId || ""),
    kind: "confirmation",
    tool: (event.target && event.target.tool) || event.tool || "",
    risk: (event.target && event.target.risk) || "",
    question: event.question || "",
    details: event.details || "",
    timeoutS: event.timeoutS,
    target: event.target || null,
    // 非空 = 这次是**让用户在卡片上挑一个**（后端 `options_source` 展开的真值），
    // 弹窗据此改画卡片而不是「同意 / 拒绝」。见 approval-modal.js 的 renderChoices。
    options: Array.isArray(event.options) ? event.options : [],
    auditId: event.auditId || "",
  };
}

function decisionFromApproval(event) {
  return {
    pendingId: String(event.pendingId || ""),
    kind: "approval",
    tool: event.tool || "",
    risk: event.risk || "",
    summary: event.summary || "",
    auditId: event.auditId || "",
  };
}

export function initChat() {
  if (chatInitialized) return;
  chatInitialized = true;

  const input = document.getElementById("composer-input");
  const send = document.getElementById("btn-send");

  // 附件条（2026-09-22 用户下达「用户会直接传图片或文件」）：三条入口 + 发送时带走。
  // 错误就地报在输入框上方（`onError`），不弹窗——上传失败是"再试一次"级别的事，
  // 不值得打断用户正在打字的动作。
  const tray = createAttachTray({
    strip: document.getElementById("attach-strip"),
    input: document.getElementById("attach-input"),
    button: document.getElementById("btn-attach"),
    getConversationId: () => state.currentConversationId,
    onError: (message) => showComposerNotice(message),
  });

  const submit = () => {
    const text = input.value.trim();
    if (isRunning) return;
    // 只有附件、没有文字也要能发（"看看这张图"是最自然的用法，而用户不该被迫配一句话）
    if (!text && tray.isEmpty()) return;
    input.value = "";
    input.style.height = "auto";
    void sendMessage(text || "（见附件）", tray.ids(), () => tray.clear());
  };
  send.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  });

  // 入口②：粘贴（截图之后最自然的动作）
  input.addEventListener("paste", (event) => {
    const files = [...(event.clipboardData?.files || [])];
    if (!files.length) return;
    event.preventDefault();
    void tray.addFiles(files);
  });

  // 入口③：拖拽（文件已经在资源管理器里选好了）
  const wrap = document.getElementById("composer-wrap");
  if (wrap) {
    const stop = (event) => { event.preventDefault(); event.stopPropagation(); };
    wrap.addEventListener("dragover", (event) => {
      stop(event);
      wrap.classList.add("is-dropping");
    });
    wrap.addEventListener("dragleave", (event) => {
      stop(event);
      wrap.classList.remove("is-dropping");
    });
    wrap.addEventListener("drop", (event) => {
      stop(event);
      wrap.classList.remove("is-dropping");
      void tray.addFiles(event.dataTransfer?.files);
    });
  }

  initToolPalette(input);
  initApprovalModal();
  initAccountDock();
  // 右下角「选择账号」**与「换一个账号」走同一条路**：界面直接请服务端登记一次
  // 新的选择，候选由服务端按账号表展开——这里只负责"要一个选择"，不自己组装候选项。
  //
  // ⚠️ 上一版「换一个账号」复用的是另一条路（把一句话放进输入框、让模型重新问）。
  // 那是 `/api/agent/selection/pick` 还不存在时的权宜之计；用户 2026-09-21 反馈
  // "点切换没有反应"——他现在预期的是**弹卡片**，而不是输入框里多一句话。
  document.addEventListener(PICK_ACCOUNT_EVENT, () => {
    void openAccountPicker();
  });
  // 认领**遗留在服务端**的待确认：没有这一步时，一条待确认只活在当前这条 SSE
  // 流里，刷新页面它就永远没人处理了——而它是一个真实写入的闸门。
  void recoverPending();
  // 刷新页面后把"当前账号"那张卡画回来（选择活在服务端，不该因为刷新就消失）
  void restoreSelection(state.currentConversationId);

  if (!state.settings.apiKey) {
    input.placeholder = "请先在「设置」中配置 DeepSeek API Key";
    input.disabled = true;
  }
  const badge = document.getElementById("chat-model-badge");
  if (badge) badge.textContent = state.settings.model || "deepseek-v4-flash";

  renderEmpty();
}

export function setChatContext() {
  const title = state.conversations.find((c) => c.id === state.currentConversationId)?.title ?? "新会话";
  document.getElementById("chat-title").textContent = title;
}

export function renderConversations() {
  const list = document.getElementById("conversation-list");
  list.innerHTML = "";
  if (!state.conversations.length) {
    const li = document.createElement("li");
    li.style.cssText = "padding:8px 12px;color:var(--text-3);font-size:12px";
    li.textContent = "暂无会话，开始提问吧";
    list.appendChild(li);
    return;
  }
  for (const conv of state.conversations) {
    const li = document.createElement("li");
    li.className = conv.id === state.currentConversationId ? "active" : "";

    const titleBtn = document.createElement("button");
    titleBtn.className = "conversation-title";
    titleBtn.title = conv.title;
    titleBtn.textContent = conv.title;
    titleBtn.addEventListener("click", () => selectConversation(conv.id));

    const delBtn = document.createElement("button");
    delBtn.className = "conversation-delete";
    delBtn.title = "删除会话";
    delBtn.textContent = "🗑";
    delBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await api.deleteConversation(conv.id);
      state.conversations = state.conversations.filter((c) => c.id !== conv.id);
      if (state.currentConversationId === conv.id) {
        state.currentConversationId = null;
        clearSelection();
        renderMessages([]);
        setChatContext();
      }
      renderConversations();
    });

    li.appendChild(titleBtn);
    li.appendChild(delBtn);
    list.appendChild(li);
  }
}

/** 右下角「选择账号」/「换一个账号」：请服务端登记一次选择，然后把卡片弹窗打开。
 *
 *  **会话必须先生成**：`/api/agent/selection/pick` 缺 conversationId 直接 400——
 *  一次记不住的选择是假的选择（用户选完、下一轮模型照样不知道用哪个）。
 *  新对话里还没有会话，所以这里先建一个：它会成为用户接下来那条消息的会话，
 *  标题也照常由第一条用户消息补上（`routes_chat._prepare_conversation`）。
 *
 *  ⚠️ `local: true` 是**必须的**（用户 2026-09-21 反馈第 4 条）：这是**用户主动**
 *  发起的选择，不是 agent 卡在等待里问的。标了它，弹窗的「稍后」就只是关掉
 *  这个窗口，**不会**在输入框上方留一条"等你拍板"的横幅——那条横幅的语义是
 *  "有人在等你答复"，而对一次自己点开的选择，它只会让人以为哪里卡住了。
 */
async function openAccountPicker() {
  if (pickingAccount) return; // 连点两下不该开出两个弹窗
  pickingAccount = true;
  try {
    let conversationId = state.currentConversationId;
    if (!conversationId) {
      const conv = await api.createConversation();
      conversationId = conv.id;
      state.currentConversationId = conversationId;
      if (!state.conversations.some((c) => c.id === conversationId)) {
        state.conversations.unshift({ id: conversationId, title: conv.title || "新会话" });
      }
      renderConversations();
      setChatContext();
    }
    const data = await api.pickSelection(conversationId);
    if (!data || !data.pendingId) throw new Error("服务端没有返回待确认 id");
    requestDecision({
      pendingId: String(data.pendingId),
      kind: "selection",
      local: true,
      question: data.question || "要用哪个账号执行？",
      details: data.details || "",
      timeoutS: data.timeoutS,
      options: Array.isArray(data.options) ? data.options : [],
    });
  } catch (error) {
    // 失败**必须可见**（本仓规矩）：印在用户刚才点的那个位置上，别处冒 toast 对不上因果
    showDockError(error.message);
  } finally {
    pickingAccount = false;
  }
}

async function selectConversation(id) {
  state.currentConversationId = id;
  renderConversations();
  setChatContext();
  // 换会话 = 换上下文：右下角那张「当前账号」跟着这一轮对话走，不跟着页面走
  void restoreSelection(id);
  const { messages } = await api.listMessages(id);
  renderMessages(messages);
}

function renderEmpty() {
  messagesEl().innerHTML = `
    <div class="empty-state">
      <div class="empty-hero">
        <h1 class="empty-line">今天的安排是什么？</h1>
      </div>
    </div>`;
}

function renderMessages(messages) {
  const el = messagesEl();
  el.innerHTML = "";
  if (!messages.length) {
    renderEmpty();
    return;
  }
  for (const msg of messages) {
    if (msg.role === "user") el.appendChild(userRow(msg.content, msg.createdAt));
    // `note` = **界面事件**（用户在右下角选了账号之类），不是谁说的话。
    // 不单独渲染的话它会掉进下面的 assistant 分支，看起来像 AKSO 说过这句话
    // ——而模型那边收到的也确实是一条 user 消息（见 routes_chat._model_history）。
    else if (msg.role === "note") el.appendChild(noteRow(msg.content, msg.createdAt));
    else el.appendChild(assistantRow(msg.content, msg.createdAt, null));
  }
  el.scrollTop = el.scrollHeight;
}

function noteRow(content, createdAt) {
  const row = document.createElement("div");
  row.className = "message-row note";
  row.innerHTML = `
    <div class="message-avatar note-avatar">⌘</div>
    <div class="message-body">
      <div class="message-meta"><span>界面操作</span>${createdAt ? `<span>${formatTime(createdAt)}</span>` : ""}</div>
      <div class="bubble bubble-note"></div>
    </div>`;
  row.querySelector(".bubble-note").textContent = content;
  return row;
}

function userRow(content, createdAt) {
  const row = document.createElement("div");
  row.className = "message-row user";
  row.innerHTML = `
    <div class="message-avatar user-avatar">我</div>
    <div class="message-body">
      <div class="message-meta"><span>我</span>${createdAt ? `<span>${formatTime(createdAt)}</span>` : ""}</div>
      <div class="bubble bubble-user"></div>
    </div>`;
  row.querySelector(".bubble-user").textContent = content;
  return row;
}

function assistantRow(content, createdAt, metricId) {
  const row = document.createElement("div");
  row.className = "message-row";
  row.innerHTML = `
    <div class="message-avatar">A</div>
    <div class="message-body">
      <div class="message-meta"><b>AKSO</b>${createdAt ? `<span>${formatTime(createdAt)}</span>` : ""}
        <div class="msg-actions"></div>
      </div>
      <div class="bubble bubble-assistant"><div class="prose"></div></div>
    </div>`;
  const proseEl = row.querySelector(".prose");
  if (content) proseEl.innerHTML = renderMarkdown(content);
  if (content && metricId) appendFeedback(row.querySelector(".message-body"), metricId);
  return row;
}

function appendFeedback(bodyEl, metricId) {
  const rowEl = document.createElement("div");
  rowEl.className = "feedback-row";
  rowEl.innerHTML = `
    <span class="feedback-label">这个回答有帮助吗？</span>
    <button class="feedback-btn" data-v="1" title="有帮助">👍</button>
    <button class="feedback-btn" data-v="0" title="没帮助">👎</button>`;
  let rated = null;
  rowEl.querySelectorAll(".feedback-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (rated !== null) return;
      rated = Number(btn.dataset.v);
      btn.classList.add(rated === 1 ? "active" : "active", rated === 1 ? "good" : "bad");
      await api.feedback(metricId, rated === 1);
    });
  });
  bodyEl.appendChild(rowEl);
}

function formatTime(ts) {
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function scrollToBottom() {
  const el = messagesEl();
  el.scrollTop = el.scrollHeight;
}

/** 输入框上方的一条就地提示（附件上传/解析失败走它）。
 *
 *  为什么不弹窗：上传失败是"换一个文件或再试一次"级别的事，而用户此刻正在打字——
 *  一个模态框会把他的输入打断，而他多半只是拖错了文件。8 秒后自动消失。 */
function showComposerNotice(message) {
  const wrap = document.getElementById("composer-wrap");
  if (!wrap) return;
  wrap.querySelectorAll(".composer-notice").forEach((el) => el.remove());
  const notice = document.createElement("div");
  notice.className = "composer-notice";
  notice.textContent = String(message || "");
  wrap.prepend(notice);
  window.setTimeout(() => notice.remove(), 8000);
}

// ———— 流式发送 ————

async function sendMessage(text, attachmentIds = [], onSent = null) {
  if (isRunning) return;
  isRunning = true;
  document.getElementById("chat-running-badge").hidden = false;

  const el = messagesEl();
  if (el.querySelector(".empty-state")) el.innerHTML = "";

  el.appendChild(userRow(text, Date.now()));
  const assistantRowEl = document.createElement("div");
  assistantRowEl.className = "message-row";
  assistantRowEl.innerHTML = `
    <div class="message-avatar">A</div>
    <div class="message-body">
      <div class="message-meta"><b>AKSO</b><span>${formatTime(Date.now())}</span><div class="msg-actions"></div></div>
      <div class="bubble bubble-assistant thinking-bubble"><span class="spin">◌</span> 正在思考…</div>
    </div>`;
  el.appendChild(assistantRowEl);
  scrollToBottom();

  const bodyEl = assistantRowEl.querySelector(".message-body");
  const metaActions = assistantRowEl.querySelector(".msg-actions");
  const trail = createToolTrail(bodyEl);
  let content = ""; // 整轮累计的全部文本：复制按钮与收尾兜底用
  let segmentText = ""; // 当前这一段文字的文本——每段只画自己的增量
  let proseEl = null; // 当前正在流入的文字段；null = 下一段文字另起气泡
  let conversationId = state.currentConversationId;
  let metricId = null;
  let usage = null; // 本轮用量与缓存命中量（usage 事件，收尾前到达）

  /** 开一段新文字。占位气泡只在它还排在消息体末尾时才复用——一旦工具卡已经落位，
   *  复用它就等于把后续文字写回卡片上方，时间顺序会颠倒。 */
  const openSegment = () => {
    const last = bodyEl.lastElementChild;
    let bubble = last && last.classList.contains("thinking-bubble") ? last : null;
    if (bubble) {
      bubble.classList.remove("thinking-bubble");
    } else {
      // 占位气泡被工具卡压在后面时不能再复用它，但也不能留着：
      // 那是「正在思考」的临时壳，留在最上面会一直显示成还没开始回答
      bodyEl.querySelectorAll(".bubble-assistant.thinking-bubble").forEach((stale) => stale.remove());
      bubble = document.createElement("div");
      bubble.className = "bubble bubble-assistant";
      bodyEl.appendChild(bubble);
    }
    bubble.innerHTML = '<div class="prose"></div><span class="typing-cursor"></span>';
    trail.endRound();
    return bubble.querySelector(".prose");
  };

  let renderTimer = 0;
  const paint = () => {
    if (!proseEl) proseEl = openSegment();
    proseEl.innerHTML = renderMarkdown(segmentText);
    scrollToBottom();
  };

  /** 「这一轮到头了」而不是「出错了」——文案最后要换个颜色。
   *  两者对用户要做的动作不一样：这个只需回一句「继续」，那个要他去排查。 */
  let softStop = false;

  /** 收段前把这段文字兑现：挂起的 rAF 要取消（否则它会在新段里用新段的文本重画），
   *  还没画过的文字要补画（rAF 没轮到就来了工具卡时，proseEl 还是空的） */
  const flushPaint = () => {
    if (renderTimer) {
      cancelAnimationFrame(renderTimer);
      renderTimer = 0;
    }
    if (!proseEl && !segmentText) return;
    if (!proseEl) proseEl = openSegment();
    proseEl.innerHTML = renderMarkdown(segmentText);
  };

  try {
    await api.streamChat({ conversationId, message: text, attachmentIds }, (event) => {
      if (event.type === "start") {
        conversationId = event.conversationId;
        state.currentConversationId = conversationId;
        // 附件已经跟着这条消息发出去了（服务端也把它们绑到了这个会话上）
        // ⇒ 输入框上的那条附件条该清空，否则用户会以为"还没发出去"
        onSent?.();
      } else if (event.type === "token") {
        content += event.text;
        segmentText += event.text;
        if (!renderTimer) {
          renderTimer = requestAnimationFrame(() => {
            renderTimer = 0;
            paint();
          });
        }
      } else if (event.type === "thought") {
        // 工具调用前模型的叙述：存进轨迹，等这一轮的卡片出现时一起落位
        trail.addThought(event.text);
      } else if (event.type === "action") {
        flushPaint(); // 先把这一段文字兑现，再收段
        segmentText = ""; // 新段的文本从零开始，否则上一轮的叙述会跟着流进新气泡
        trail.addAction(event);
        proseEl = null; // 卡片之后的文字另起一段，DOM 顺序 = 发生顺序
        scrollToBottom();
      } else if (event.type === "observation") {
        trail.applyObservation(event);
        scrollToBottom();
      } else if (event.type === "approval_required") {
        // 风险门拦下：轨迹留一条回执，**交互交给弹窗**（内联卡片会随输出滚走，
        // 而它是一次真实写入的闸门）
        flushPaint();
        segmentText = "";
        trail.addApproval(event);
        requestDecision(decisionFromApproval(event));
        proseEl = null;
        scrollToBottom();
      } else if (event.type === "confirm_required") {
        // 模型主动"先问再做"：弹窗出现时后端**正卡在等待里**，
        // 用户在弹窗里点完模型自己继续，不需要再发一条消息
        flushPaint();
        segmentText = "";
        trail.addConfirm(event);
        requestDecision(decisionFromConfirm(event));
        proseEl = null;
        scrollToBottom();
      } else if (event.type === "confirm_resolved") {
        trail.settleConfirm(event);
        scrollToBottom();
      } else if (event.type === "final") {
        content = event.content;
      } else if (event.type === "error") {
        // 「这一轮到头了」与「真出错了」要分开渲染：前者只需要用户回一句「继续」，
        // 后者需要他去排查。两者都印成红色"错误："会让人对着一个其实能接着走的
        // 状态去查日志（`reason` 由 loop.py 给出，见 FACTORY-AGENT-GAP 问题①）。
        content = event.reason === "budget_exhausted"
          ? `⏸ ${event.message}`
          : event.reason === "output_truncated"
            ? `✂ ${event.message}`
            : `错误：${event.message}`;
        softStop = event.reason === "budget_exhausted" || event.reason === "output_truncated";
      } else if (event.type === "usage") {
        // 用量在 final/error 之后单独发一条：命中量要等所有模型调用（含摘要重算）
        // 都结束才是完整答案，提前显示会给出一个偏低的数字
        usage = event.usage || null;
      } else if (event.type === "done") {
        metricId = event.metricId || null;
        if (event.titleUpdated && conversationId) {
          const conv = state.conversations.find((c) => c.id === conversationId);
          const title = text.slice(0, 30);
          if (conv) { conv.title = title; }
          else state.conversations.unshift({ id: conversationId, title });
          renderConversations();
          setChatContext();
        }
      }
    });
  } catch (error) {
    content = `错误：${error.message}`;
  }

  flushPaint();
  // 最后一段才装最终回答：中间那些段落留着模型调用工具前的叙述，
  // 用收尾内容覆盖它们会把「为什么调这个工具」的来龙去脉抹掉
  if (!proseEl) proseEl = openSegment();
  proseEl.innerHTML = renderMarkdown(content || "（无内容）");
  proseEl.classList.toggle("prose-soft-stop", softStop);
  bodyEl.querySelectorAll(".typing-cursor").forEach((cursor) => cursor.remove());
  // 兜底：还剩着「正在思考…」的空壳，说明没有任何一段文字接管它，留着就是一条假状态
  bodyEl.querySelectorAll(".bubble-assistant.thinking-bubble").forEach((stale) => stale.remove());

  // 复制按钮
  const copyBtn = document.createElement("button");
  copyBtn.className = "icon-btn";
  copyBtn.title = "复制回答";
  copyBtn.textContent = "⧉";
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(content);
      copyBtn.textContent = "✓";
      copyBtn.classList.add("copy-done");
      setTimeout(() => { copyBtn.textContent = "⧉"; copyBtn.classList.remove("copy-done"); }, 1500);
    } catch { /* 剪贴板不可用时静默 */ }
  });
  metaActions.appendChild(copyBtn);

  if (metricId) appendFeedback(bodyEl, metricId);
  if (usage && usage.promptTokens) metaActions.appendChild(usageChip(usage, metaActions));

  isRunning = false;
  document.getElementById("chat-running-badge").hidden = true;
  scrollToBottom();
}

/** 缓存命中徽章：把"这次回答有多少输入是从缓存里读的"直接摆在回答旁边。
 *
 *  为什么放在这里而不是只做设置页统计：命中率是**每次请求都在变**的数字，
 *  用户看不到就不知道自己的用法（长会话 vs 每次新开）对成本的影响。
 *  DeepSeek 的命中价是未命中价的 1/50，所以这个百分比直接等于省钱比例。 */
function usageChip(usage, host) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "usage-chip";
  const rate = Math.round((usage.hitRate || 0) * 100);
  chip.dataset.level = rate >= 60 ? "good" : rate >= 20 ? "mid" : "low";
  chip.textContent = `缓存 ${rate}%`;
  chip.title = [
    `输入 ${usage.promptTokens} token（命中 ${usage.cacheHitTokens} / 未命中 ${usage.cacheMissTokens}）`,
    `输出 ${usage.completionTokens} token`,
    `本轮 ${usage.calls} 次模型调用${usage.summaryCalls ? `（含 ${usage.summaryCalls} 次历史摘要重算）` : ""}`,
  ].join("\n");
  chip.addEventListener("click", () => {
    const detail = document.createElement("span");
    detail.className = "usage-detail";
    detail.textContent = chip.title;
    host.appendChild(detail);
    chip.remove();
  });
  return chip;
}
