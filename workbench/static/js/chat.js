/** 合规对话视图：消息渲染 + SSE 流式 + 审计轨迹 + 反馈 */

import { api } from "./api.js";
import { renderMarkdown } from "./markdown.js";
import { createToolTrail, initToolPalette } from "./chat-tools.js";
import { state } from "./app.js";

const messagesEl = () => document.getElementById("messages");
let isRunning = false;
let chatInitialized = false;

export function initChat() {
  if (chatInitialized) return;
  chatInitialized = true;

  const input = document.getElementById("composer-input");
  const send = document.getElementById("btn-send");
  const submit = () => {
    const text = input.value.trim();
    if (!text || isRunning) return;
    input.value = "";
    input.style.height = "auto";
    void sendMessage(text);
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
  initToolPalette(input);

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

async function selectConversation(id) {
  state.currentConversationId = id;
  renderConversations();
  setChatContext();
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
    else el.appendChild(assistantRow(msg.content, msg.createdAt, null));
  }
  el.scrollTop = el.scrollHeight;
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

// ———— 流式发送 ————

async function sendMessage(text) {
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
    await api.streamChat({ conversationId, message: text }, (event) => {
      if (event.type === "start") {
        conversationId = event.conversationId;
        state.currentConversationId = conversationId;
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
        flushPaint();
        segmentText = "";
        trail.addApproval(event);
        proseEl = null;
        scrollToBottom();
      } else if (event.type === "confirm_required") {
        // 模型主动"先问再做"：这张卡出现时后端**正卡在等待里**，
        // 用户点完模型自己继续，不需要再发一条消息
        flushPaint();
        segmentText = "";
        trail.addConfirm(event);
        proseEl = null;
        scrollToBottom();
      } else if (event.type === "confirm_resolved") {
        trail.settleConfirm(event);
        scrollToBottom();
      } else if (event.type === "final") {
        content = event.content;
      } else if (event.type === "error") {
        content = `错误：${event.message}`;
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
