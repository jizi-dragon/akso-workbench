/** 全局状态 + 视图路由 + 启动引导 */

import { api } from "./api.js";
import { initChat, renderConversations, setChatContext } from "./chat.js";
import { initSettings, fillSettings } from "./settings.js";

export const state = {
  conversations: [],
  currentConversationId: null,
  stats: {},
  settings: {},
};

const SPLASH_STAGES = ["连接本地服务", "加载工作台", "启动助手"];

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
  const el = document.getElementById("engine-stats");
  if (el) el.textContent = "就绪";
}

export function switchView(name) {
  for (const view of ["chat", "settings"]) {
    document.getElementById(`view-${view}`).hidden = view !== name;
    document.getElementById(`nav-${view}`).classList.toggle("active", view === name);
  }
  if (name === "settings") fillSettings();
}

const SPLASH_KEY = "akso-splash-seen";

async function bootstrap() {
  const overlay = document.getElementById("splash-overlay");
  // 同一会话内只播一次启动动画：从平台模块返回工作台时直接进入（避免强制重看）
  const seen = sessionStorage.getItem(SPLASH_KEY) === "1";
  if (seen) {
    overlay?.remove();
  }
  const splashTimer = seen ? null : cycleSplash();
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
  } catch (error) {
    const el = document.getElementById("splash-status-text");
    if (el) el.textContent = `初始化失败：${error.message}`;
    console.error(error);
    if (splashTimer) setTimeout(() => clearInterval(splashTimer), 100);
    return;
  }
  if (seen) {
    sessionStorage.setItem(SPLASH_KEY, "1");
    return;
  }
  // 让启动动画至少播放 1.1s
  setTimeout(() => {
    if (splashTimer) clearInterval(splashTimer);
    const el = document.getElementById("splash-overlay");
    el?.classList.add("hidden");
    setTimeout(() => el?.remove(), 600);
    sessionStorage.setItem(SPLASH_KEY, "1");
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
