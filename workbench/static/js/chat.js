/** 合规对话视图：消息渲染 + SSE 流式 + 审计轨迹 + 反馈 */

import { api } from "./api.js";
import { renderMarkdown } from "./markdown.js";
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

function escapeText(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
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
  const bubbleEl = assistantRowEl.querySelector(".bubble-assistant");
  const metaActions = assistantRowEl.querySelector(".msg-actions");
  let content = "";
  const toolCalls = [];
  let toolTrail = null;

  const ensureProse = () => {
    if (!bubbleEl.querySelector(".prose")) {
      bubbleEl.classList.remove("thinking-bubble");
      bubbleEl.innerHTML = '<div class="prose"></div><span class="typing-cursor"></span>';
    }
    return bubbleEl.querySelector(".prose");
  };
  let renderTimer = 0;
  const paint = () => {
    const prose = ensureProse();
    prose.innerHTML = renderMarkdown(content);
    scrollToBottom();
  };

  let conversationId = state.currentConversationId;
  let metricId = null;
  let lastThought = null;

  try {
    await api.streamChat({ conversationId, message: text }, (event) => {
      if (event.type === "start") {
        conversationId = event.conversationId;
        state.currentConversationId = conversationId;
      } else if (event.type === "token") {
        content += event.text;
        if (!renderTimer) {
          renderTimer = requestAnimationFrame(() => {
            renderTimer = 0;
            paint();
          });
        }
      } else if (event.type === "thought") {
        // 工具调用前模型的叙述：追加到审计轨迹顶部（有轨迹时才展示）
        lastThought = event.text;
      } else if (event.type === "action") {
        if (!toolTrail) {
          toolTrail = document.createElement("div");
          toolTrail.className = "tool-trail";
          toolTrail.innerHTML = '<div class="tool-trail-label">审计轨迹 · 工具调用</div>';
          if (lastThought) {
            const narration = document.createElement("div");
            narration.className = "tool-thought";
            narration.textContent = `💭 ${lastThought}`;
            toolTrail.appendChild(narration);
          }
          bodyEl.appendChild(toolTrail);
        }
        const card = document.createElement("div");
        card.className = "tool-card";
        card.innerHTML = `
          <div class="tool-card-header">
            <span class="tool-icon">⚡</span>
            <span class="tool-name">${escapeText(event.call.name)}</span>
            <span class="tool-status running"><span class="spin">◌</span> 执行中</span>
          </div>`;
        card.dataset.args = event.call.arguments || "";
        toolCalls.push(card);
        toolTrail.appendChild(card);
      } else if (event.type === "observation") {
        const card = toolCalls[toolCalls.length - 1];
        if (card) {
          const status = card.querySelector(".tool-status");
          status.className = `tool-status ${event.result.is_error ? "error" : "done"}`;
          status.innerHTML = event.result.is_error ? "⚠ 异常" : "✓ 完成";
          const body = document.createElement("div");
          body.className = "tool-card-body";
          body.textContent = event.result.content || "";
          body.hidden = true;
          card.appendChild(body);
          card.querySelector(".tool-card-header").addEventListener("click", () => {
            body.hidden = !body.hidden;
          });
        }
      } else if (event.type === "final") {
        content = event.content;
      } else if (event.type === "error") {
        content = `错误：${event.message}`;
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

  if (renderTimer) cancelAnimationFrame(renderTimer);
  bubbleEl.classList.remove("thinking-bubble");
  bubbleEl.innerHTML = `<div class="prose">${renderMarkdown(content || "（无内容）")}</div>`;

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

  isRunning = false;
  document.getElementById("chat-running-badge").hidden = true;
  scrollToBottom();
}
