/** 设置视图：DeepSeek Key / 模型 / 温度 / Embedding 配置 */

import { api } from "./api.js";
import { state } from "./app.js";

let settingsInitialized = false;

const MODEL_OPTIONS = [
  { id: "deepseek-v4-flash", tag: "经济版", desc: "响应快、成本低：日常问答、工具调用与知识抽取" },
  { id: "deepseek-v4-pro", tag: "旗舰版", desc: "深度推理更强：复杂合规架构分析与多步推演" },
];

export function initSettings() {
  if (settingsInitialized) return;
  settingsInitialized = true;

  const grid = document.getElementById("model-grid");
  grid.innerHTML = MODEL_OPTIONS.map(
    (option) => `
    <button type="button" class="model-card" data-model="${option.id}">
      <div class="model-card-head">
        <span class="mono">${option.id}</span>
        <span class="badge ${option.tag === "旗舰版" ? "badge-lime" : "badge-teal"}">${option.tag}</span>
      </div>
      <span class="model-card-desc">${option.desc}</span>
      <span class="model-check" hidden>✓ 使用中</span>
    </button>`
  ).join("");
  grid.querySelectorAll(".model-card").forEach((card) => {
    card.addEventListener("click", () => {
      state.settings.model = card.dataset.model;
      paintModelGrid();
    });
  });

  const tempInput = document.getElementById("set-temperature");
  tempInput.addEventListener("input", () => {
    document.getElementById("temp-value").textContent = Number(tempInput.value).toFixed(1);
  });

  document.getElementById("btn-toggle-key").addEventListener("click", () => {
    const input = document.getElementById("set-api-key");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    document.getElementById("btn-toggle-key").textContent = show ? "隐藏" : "显示";
  });

  document.getElementById("btn-save-settings").addEventListener("click", async () => {
    const result = await api.saveSettings({
      apiKey: document.getElementById("set-api-key").value.trim() || null,
      model: state.settings.model,
      temperature: Number(document.getElementById("set-temperature").value),
    });
    state.settings = result.settings;
    flashButton("btn-save-settings", "✓ 已保存");
  });

  document.getElementById("btn-save-emb").addEventListener("click", async () => {
    const result = await api.saveEmbedding({
      apiKey: document.getElementById("set-emb-key").value.trim() || null,
      model: document.getElementById("set-emb-model").value.trim() || null,
      baseUrl: document.getElementById("set-emb-base").value.trim() || null,
    });
    state.settings = result.settings;
    flashButton("btn-save-emb", "✓ 已保存");
  });

  document.getElementById("btn-test-emb").addEventListener("click", async () => {
    const slot = document.getElementById("emb-test-result");
    slot.innerHTML = '<div class="embed-test"><span class="spin">◌</span> 测试中…</div>';
    const result = await api.testEmbedding();
    slot.innerHTML = `<div class="embed-test ${result.ok ? "ok" : "fail"}">${result.ok ? "✓" : "⚠"} ${result.message}</div>`;
  });
}

export function fillSettings() {
  const settings = state.settings;
  document.getElementById("set-api-key").value = settings.apiKey || "";
  document.getElementById("set-temperature").value = settings.temperature ?? 0.7;
  document.getElementById("temp-value").textContent = Number(settings.temperature ?? 0.7).toFixed(1);
  document.getElementById("set-emb-key").value = settings.embedding?.apiKey || "";
  document.getElementById("set-emb-model").value = settings.embedding?.model || "qwen3.7-text-embedding";
  document.getElementById("set-emb-base").value =
    settings.embedding?.baseUrl || "https://dashscope.aliyuncs.com/compatible-mode/v1";
  paintModelGrid();
}

function paintModelGrid() {
  document.querySelectorAll(".model-card").forEach((card) => {
    const active = card.dataset.model === state.settings.model;
    card.classList.toggle("active", active);
    card.querySelector(".model-check").hidden = !active;
  });
}

function flashButton(id, text) {
  const btn = document.getElementById(id);
  const original = btn.innerHTML;
  btn.innerHTML = text;
  setTimeout(() => { btn.innerHTML = original; }, 1600);
}
