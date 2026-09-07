/** 全局状态 + 视图路由 + 启动引导 */

import { api } from "./api.js";
import { initChat, renderConversations, setChatContext } from "./chat.js";
import { initKnowledge, refreshKnowledge } from "./knowledge.js";
import { initSettings, fillSettings } from "./settings.js";

export const state = {
  conversations: [],
  currentConversationId: null,
  stats: {},
  settings: {},
};

const SPLASH_STAGES = ["加载本地知识库", "校准合规语义引擎", "启动监管智能体"];

function cycleSplash() {
  let index = 0;
  const el = document.getElementById("splash-status-text");
  const timer = setInterval(() => {
    index = (index + 1) % SPLASH_STAGES.length;
    if (el) el.textContent = `${SPLASH_STAGES[index]}…`;
  }, 950);
  return timer;
}

export function updateEngineStats() {
  const s = state.stats;
  const el = document.getElementById("engine-stats");
  if (el) el.textContent = `知识引擎在线 · ${s.documents ?? 0} 文件 / ${s.chunks ?? 0} 块`;
  const count = document.getElementById("nav-knowledge-count");
  if (count) count.textContent = String(s.documents ?? 0);
  const adopt = document.getElementById("stat-adopt");
  if (adopt) {
    if (s.qaTotal > 0) {
      adopt.hidden = false;
      adopt.textContent = `采纳率 ${s.adoptRate ?? "—"}%`;
      adopt.title = `近 ${s.qaTotal} 次问答`;
    } else {
      adopt.hidden = true;
    }
  }
}

export function switchView(name) {
  for (const view of ["chat", "knowledge", "settings"]) {
    document.getElementById(`view-${view}`).hidden = view !== name;
    document.getElementById(`nav-${view}`).classList.toggle("active", view === name);
  }
  if (name === "knowledge") refreshKnowledge();
  if (name === "settings") fillSettings();
}

async function bootstrap() {
  const splashTimer = cycleSplash();
  try {
    const data = await api.bootstrap();
    state.conversations = data.conversations;
    state.stats = data.stats;
    state.settings = data.settings;
    updateEngineStats();
    renderConversations();
    setChatContext();
    initChat();
    initSettings();
    initKnowledge();
  } catch (error) {
    const el = document.getElementById("splash-status-text");
    if (el) el.textContent = `初始化失败：${error.message}`;
    console.error(error);
    setTimeout(() => clearInterval(splashTimer), 100);
    return;
  }
  // 让启动动画至少播放 1.1s
  setTimeout(() => {
    clearInterval(splashTimer);
    const overlay = document.getElementById("splash-overlay");
    overlay.classList.add("hidden");
    setTimeout(() => overlay.remove(), 600);
  }, 1100);
}

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => switchView(btn.dataset.view));
});

document.getElementById("btn-new-conversation").addEventListener("click", async () => {
  const conv = await api.createConversation();
  state.conversations.unshift(conv);
  state.currentConversationId = conv.id;
  renderConversations();
  setChatContext();
  switchView("chat");
});

bootstrap();
