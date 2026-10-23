/** 设置视图：DeepSeek Key / 模型 / 温度 / **系统提示词** / **缓存命中率** */

import { api } from "./api.js";
import { state } from "./app.js";

let settingsInitialized = false;

const MODEL_OPTIONS = [
  { id: "deepseek-flash", tag: "经济版", desc: "V4.1-Flash：响应快、成本低，日常问答与工具调用" },
  { id: "deepseek-v4-pro", tag: "旗舰版", desc: "深度推理更强：复杂架构分析与多步推演" },
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

  const stepsInput = document.getElementById("set-max-steps");
  stepsInput.addEventListener("input", () => {
    document.getElementById("max-steps-value").textContent = String(stepsInput.value);
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
      maxSteps: Number(document.getElementById("set-max-steps").value),
    });
    state.settings = result.settings;
    // 服务端会把越界值夹回范围内，所以**保存后必须回填**——否则滑块停在 999，
    // 而实际生效的是 200，"我设的"和"跑的是"就对不上了
    fillSteps();
    flashButton("btn-save-settings", "✓ 已保存");
  });

  initPromptEditor();
  initToolScope();
  initSchedules();
  initGoals();
  initCachePanel();
  initMcpPanel();
}

/* ---------------------------------------------------------------- 长期目标 */

function initGoals() {
  const list = document.getElementById("goal-list");
  const meta = document.getElementById("goal-meta");
  if (!list) return;

  const render = (items) => {
    if (!items.length) {
      list.innerHTML = '<div class="hint" style="padding:8px">还没有长期目标</div>';
      return;
    }
    list.innerHTML = items.map((goal) => `
      <div class="schedule-row">
        <div class="schedule-main">
          <b>${esc(goal.title)}</b>
          <small>${esc(goal.objective)}</small>
          <small>${esc(goal.status)} · ${goal.rounds}/${goal.maxRounds} 轮</small>
        </div>
        ${goal.status === "active"
          ? `<button class="btn-ghost btn-sm" type="button" data-goal-continue="${esc(goal.id)}">继续一轮</button>
             <button class="btn-ghost btn-sm" type="button" data-goal-advance="${esc(goal.id)}">自动推进</button>
             <button class="btn-ghost btn-sm" type="button" data-goal-status="paused" data-id="${esc(goal.id)}">暂停</button>`
          : goal.status === "paused"
            ? `<button class="btn-ghost btn-sm" type="button" data-goal-status="active" data-id="${esc(goal.id)}">恢复</button>`
            : ""}
        ${goal.status !== "done"
          ? `<button class="btn-ghost btn-sm" type="button" data-goal-status="done" data-id="${esc(goal.id)}">完成</button>`
          : ""}
        <button class="btn-ghost btn-sm" type="button" data-goal-del="${esc(goal.id)}">删除</button>
      </div>`).join("");
  };

  const load = async () => {
    try {
      const data = await api.listGoals();
      render(data.goals || []);
    } catch (err) {
      list.innerHTML = `<div class="hint" style="color:#f85149;padding:8px">加载失败：${esc(err.message)}</div>`;
    }
  };

  document.getElementById("btn-goal-create").addEventListener("click", async () => {
    const title = document.getElementById("goal-title").value.trim();
    const objective = document.getElementById("goal-objective").value.trim();
    const maxRounds = Number(document.getElementById("goal-max-rounds").value);
    if (!title || !objective) {
      meta.textContent = "标题和目标描述都不能为空";
      return;
    }
    try {
      await api.createGoal({ title, objective, maxRounds });
      document.getElementById("goal-title").value = "";
      document.getElementById("goal-objective").value = "";
      meta.textContent = "✓ 已创建";
      await load();
    } catch (err) {
      meta.textContent = `✗ 创建失败：${err.message}`;
    }
  });

  list.addEventListener("click", async (event) => {
    const statusBtn = event.target.closest("[data-goal-status]");
    const continueBtn = event.target.closest("[data-goal-continue]");
    const advanceBtn = event.target.closest("[data-goal-advance]");
    const delBtn = event.target.closest("[data-goal-del]");
    try {
      if (advanceBtn) {
        if (!window.confirm("自动推进到剩余轮次上限？每轮都会调用模型，可能耗时并产生费用。")) return;
        meta.textContent = "正在自动推进（只读，可能多轮）…";
        const result = await api.advanceGoal(advanceBtn.dataset.goalAdvance, null);
        meta.textContent = `✓ 已推进 ${result.rounds} 轮（${result.goal.rounds}/${result.goal.maxRounds}）`;
        await load();
      } else if (continueBtn) {
        meta.textContent = "正在推进一轮（只读，可能要一会儿）…";
        const result = await api.continueGoal(continueBtn.dataset.goalContinue);
        meta.textContent = result.error
          ? `✗ 本轮失败：${result.error}`
          : `✓ 已推进到第 ${result.goal.rounds}/${result.goal.maxRounds} 轮，结果在会话记录里`;
        await load();
      } else if (statusBtn) {
        await api.patchGoal(statusBtn.dataset.id, { status: statusBtn.dataset.goalStatus });
        await load();
      } else if (delBtn) {
        if (!window.confirm("删除这个长期目标？")) return;
        await api.deleteGoal(delBtn.dataset.goalDel);
        await load();
      }
    } catch (err) {
      meta.textContent = `✗ 操作失败：${err.message}`;
    }
  });

  load();
}

/* ---------------------------------------------------------------- 定时任务 */

function initSchedules() {
  const list = document.getElementById("sch-list");
  const meta = document.getElementById("sch-meta");
  if (!list) return;

  const fmtTime = (ms) => ms ? new Date(Number(ms)).toLocaleString() : "—";
  const render = (items) => {
    if (!items.length) {
      list.innerHTML = '<div class="hint" style="padding:8px">还没有定时任务</div>';
      return;
    }
    list.innerHTML = items.map((task) => `
      <div class="schedule-row">
        <div class="schedule-main">
          <b>${esc(task.title)}</b>
          <small>每 ${Math.round(task.intervalS / 60)} 分钟 · 下次 ${fmtTime(task.nextRunAt)}
            ${task.lastStatus ? ` · 上次 ${esc(task.lastStatus)}` : ""}</small>
          ${task.lastError ? `<small class="mcp-error">${esc(task.lastError)}</small>` : ""}
        </div>
        <button class="btn-ghost btn-sm" type="button" data-sch-toggle="${esc(task.id)}">
          ${task.enabled ? "暂停" : "启用"}
        </button>
        <button class="btn-ghost btn-sm" type="button" data-sch-run="${esc(task.id)}">运行</button>
        <button class="btn-ghost btn-sm" type="button" data-sch-del="${esc(task.id)}">删除</button>
      </div>`).join("");
  };

  const load = async () => {
    try {
      const data = await api.listSchedules();
      render(data.schedules || []);
    } catch (err) {
      list.innerHTML = `<div class="hint" style="color:#f85149;padding:8px">加载失败：${esc(err.message)}</div>`;
    }
  };

  document.getElementById("btn-sch-create").addEventListener("click", async () => {
    const title = document.getElementById("sch-title").value.trim();
    const prompt = document.getElementById("sch-prompt").value.trim();
    const intervalS = Number(document.getElementById("sch-interval").value)
      * Number(document.getElementById("sch-unit").value);
    if (!title || !prompt) {
      meta.textContent = "标题和提示词都不能为空";
      return;
    }
    try {
      await api.createSchedule({ title, prompt, intervalS });
      document.getElementById("sch-title").value = "";
      document.getElementById("sch-prompt").value = "";
      meta.textContent = "✓ 已创建";
      await load();
    } catch (err) {
      meta.textContent = `✗ 创建失败：${err.message}`;
    }
  });

  list.addEventListener("click", async (event) => {
    const toggle = event.target.closest("[data-sch-toggle]");
    const run = event.target.closest("[data-sch-run]");
    const del = event.target.closest("[data-sch-del]");
    try {
      if (toggle) {
        const id = toggle.dataset.schToggle;
        const task = (await api.listSchedules()).schedules.find((item) => item.id === id);
        await api.patchSchedule(id, { enabled: !task.enabled });
        await load();
      } else if (run) {
        meta.textContent = "正在执行（只读，可能要一会儿）…";
        const result = await api.runSchedule(run.dataset.schRun);
        meta.textContent = result.status === "ok"
          ? "✓ 已执行，结果在会话记录里"
          : `✗ 执行失败：${result.error || "未知错误"}`;
        await load();
      } else if (del) {
        if (!window.confirm("删除这个定时任务？")) return;
        await api.deleteSchedule(del.dataset.schDel);
        await load();
      }
    } catch (err) {
      meta.textContent = `✗ 操作失败：${err.message}`;
    }
  });

  load();
}

/* ---------------------------------------------------------------- 工具范围 */

function initToolScope() {
  const list = document.getElementById("tool-scope-list");
  const meta = document.getElementById("tool-scope-meta");
  if (!list) return;
  let allowed = null;
  let tools = [];

  const render = () => {
    if (!tools.length) {
      list.innerHTML = '<small class="hint">没有可用工具</small>';
      return;
    }
    list.innerHTML = tools.map((tool) => {
      const checked = allowed === null || allowed.includes(tool.name);
      return `
        <label class="tool-scope-row" title="${esc(tool.description || "")}">
          <input type="checkbox" value="${esc(tool.name)}" ${checked ? "checked" : ""} />
          <span class="mono">${esc(tool.name)}</span>
          <span class="badge ${tool.risk === "read" ? "badge-teal" : "badge-red"}">${esc(tool.risk)}</span>
          <small>${esc((tool.description || "").slice(0, 80))}</small>
        </label>`;
    }).join("");
  };

  const load = async () => {
    try {
      const data = await api.getToolScope();
      allowed = data.allowed;
      tools = data.tools || [];
      render();
      meta.textContent = allowed === null
        ? `全部 ${tools.length} 个工具`
        : `已限制为 ${allowed.length} / ${tools.length}`;
    } catch (err) {
      list.innerHTML = `<small class="hint" style="color:#f85149">加载失败：${esc(err.message)}</small>`;
    }
  };

  document.getElementById("btn-save-tools").addEventListener("click", async () => {
    const checked = [...list.querySelectorAll('input[type="checkbox"]:checked')].map((el) => el.value);
    const next = checked.length === tools.length ? null : checked;
    try {
      const data = await api.saveToolScope(next);
      allowed = data.allowed;
      meta.textContent = allowed === null
        ? `已恢复全部 ${tools.length} 个工具`
        : `已保存 ${allowed.length} / ${tools.length} 个工具`;
      render();
      flashButton("btn-save-tools", "✓ 已保存");
    } catch (err) {
      meta.textContent = `✗ 保存失败：${err.message}`;
    }
  });

  document.getElementById("btn-reset-tools").addEventListener("click", async () => {
    try {
      await api.saveToolScope(null);
      await load();
      flashButton("btn-reset-tools", "✓ 已恢复");
    } catch (err) {
      meta.textContent = `✗ 恢复失败：${err.message}`;
    }
  });

  load();
}

/* ---------------------------------------------------------------- 系统提示词 */

function initPromptEditor() {
  const textarea = document.getElementById("set-system-prompt");
  const badge = document.getElementById("prompt-custom-badge");
  const counter = document.getElementById("prompt-chars");

  // 输入即更新字数与「已自定义」徽章：用户要能立刻看出"我现在到底是不是在用默认"
  textarea.addEventListener("input", () => {
    const value = textarea.value.trim();
    counter.textContent = `${value.length} 字`;
    badge.hidden = !value;
  });

  // 「填入默认内容」只把基线文本填进框里，**不保存**——保存必须显式点按钮，
  // 否则误点一下就改动线上行为，而用户可能只是想把默认内容拿去改一改
  document.getElementById("btn-prompt-default").addEventListener("click", async () => {
    const current = await api.getPrompt();
    textarea.value = current.default;
    textarea.dispatchEvent(new Event("input"));
  });

  // 显式保存（不做输入防抖自动保存：改提示词会打断缓存前缀，得让用户自己决定何时生效）
  document.getElementById("btn-save-prompt").addEventListener("click", async () => {
    const button = document.getElementById("btn-save-prompt");
    try {
      const saved = await api.savePrompt(textarea.value);
      state.settings.systemPrompt = saved;
      // 保存的是"用户版"（空 = 用基线），所以回填时用**生效值**，
      // 否则留空表示默认的用户会看到框里空空如也、以为提示词被清掉了
      textarea.value = saved.prompt;
      textarea.dispatchEvent(new Event("input"));
      flashButton("btn-save-prompt", saved.custom ? "✓ 已保存" : "✓ 已恢复默认");
    } catch (error) {
      button.textContent = `保存失败：${error.message}`;
      setTimeout(() => { button.textContent = "保存提示词"; }, 2600);
    }
  });
}

function paintPrompt() {
  const prompt = state.settings.systemPrompt || {};
  const textarea = document.getElementById("set-system-prompt");
  // 框里永远显示**当前生效**的内容：未自定义时就是基线，便于直接看着改
  textarea.value = prompt.prompt || "";
  textarea.dispatchEvent(new Event("input"));
}

/* ---------------------------------------------------------------- 缓存面板 */

function initCachePanel() {
  document.getElementById("btn-cache-refresh").addEventListener("click", () => loadCache(true));
}

async function loadCache() {
  const box = document.getElementById("cache-stats");
  const note = document.getElementById("cache-note");
  if (!box) return;
  try {
    paintCache(await api.getCache());
  } catch (error) {
    box.textContent = `读取失败：${error.message}`;
    note.textContent = "";
  }
}

function paintCache(data) {
  const box = document.getElementById("cache-stats");
  const note = document.getElementById("cache-note");
  const hit = data.cacheHitTokens || 0;
  const miss = data.cacheMissTokens || 0;
  const total = hit + miss;

  if (!data.answers || !total) {
    box.innerHTML =
      '<div class="cache-empty">最近 7 天还没有对话记录。发一条消息后这里会显示真实的缓存命中量。</div>';
    note.textContent = "";
    return;
  }

  const rate = Math.round((data.hitRate || 0) * 100);
  const saved = Math.round((data.savedRatio || 0) * 100);
  box.innerHTML = `
    <div class="cache-metric cache-metric-main">
      <span class="cache-metric-value">${rate}%</span>
      <span class="cache-metric-label">缓存命中率</span>
      <div class="cache-bar"><div class="cache-bar-fill" style="width:${rate}%"></div></div>
    </div>
    <div class="cache-metric"><span class="cache-metric-value">${fmtTokens(hit)}</span>
      <span class="cache-metric-label">命中 token</span></div>
    <div class="cache-metric"><span class="cache-metric-value">${fmtTokens(miss)}</span>
      <span class="cache-metric-label">未命中 token</span></div>
    <div class="cache-metric"><span class="cache-metric-value">${fmtTokens(data.completionTokens)}</span>
      <span class="cache-metric-label">输出 token</span></div>
    <div class="cache-metric"><span class="cache-metric-value">${data.answers}</span>
      <span class="cache-metric-label">回答数</span></div>`;
  // 摘要重算次数单独提示：它是"前缀被打断"的直接证据，命中率掉了先看它
  note.textContent = data.summaryCalls
    ? `按命中价折算，约省下 ${saved}% 的输入费用；期间重算历史摘要 ${data.summaryCalls} 次（每次都会打断前缀，越少越好）`
    : `按命中价折算，约省下 ${saved}% 的输入费用；历史摘要未重算过（前缀一直稳定）`;
}

function fmtTokens(value) {
  const n = Number(value || 0);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

/* ---------------------------------------------------------------- 填充与保存 */

export function fillSettings() {
  const settings = state.settings;
  document.getElementById("set-api-key").value = settings.apiKey || "";
  document.getElementById("set-temperature").value = settings.temperature ?? 0.7;
  document.getElementById("temp-value").textContent = Number(settings.temperature ?? 0.7).toFixed(1);
  // 模型名做一次归一：库里可能存着已退役的 `deepseek-v4-flash`（官方兼容路由到
  // V4.1-Flash），卡片上没有它就一个都不高亮，看着像"没选模型"
  if (settings.model && !MODEL_OPTIONS.some((m) => m.id === settings.model)) {
    settings.model = "deepseek-flash";
  }
  paintModelGrid();
  paintPrompt();
  fillSteps();
  loadCache();
}

/** 步数滑块：范围与默认值都从服务端来（**不在这里写死第二份**——
 *  前后端各写一个 40，改一处漏一处就是"界面上写着 40、实际跑的是 12"）。 */
function fillSteps() {
  const settings = state.settings || {};
  const range = Array.isArray(settings.maxStepsRange) ? settings.maxStepsRange : [4, 200];
  const value = Number(settings.maxSteps ?? settings.maxStepsDefault ?? range[1]);
  const input = document.getElementById("set-max-steps");
  input.min = String(range[0]);
  input.max = String(range[1]);
  input.value = String(value);
  document.getElementById("max-steps-value").textContent = String(value);
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

/* ---------------------------------------------------------------- MCP 面板 */

function initMcpPanel() {
  document.getElementById("btn-mcp-reload").addEventListener("click", () => loadMcp(true));
  document.getElementById("mcp-servers").addEventListener("click", async (event) => {
    const activate = event.target.closest("[data-mcp-activate]");
    const button = event.target.closest("[data-mcp-reconnect]") || activate;
    if (!button) return;
    const name = activate ? button.dataset.mcpActivate : button.dataset.mcpReconnect;
    button.disabled = true;
    button.textContent = activate ? "加载中…" : "重连中…";
    try {
      await (activate ? api.activateMcp(name) : api.reconnectMcp(name));
      await loadMcp(false);
    } catch (error) {
      const note = document.getElementById("mcp-note");
      if (note) note.textContent = `重连 ${name} 失败：${error.message}`;
      button.disabled = false;
      button.textContent = activate ? "加载" : "重连";
    }
  });
  void loadMcp(false);
}

async function loadMcp(reload) {
  const box = document.getElementById("mcp-servers");
  const note = document.getElementById("mcp-note");
  if (!box) return;
  if (reload) note.textContent = "正在重连…（启动子进程可能要几秒）";
  try {
    const data = reload ? await api.reloadMcp() : await api.getMcpServers();
    paintMcp(data);
    if (reload) note.textContent = "已重新读取配置";
  } catch (error) {
    box.innerHTML = `<div class="cache-empty">读取失败：${esc(error.message)}</div>`;
    note.textContent = "";
  }
}

function paintMcp(data) {
  const box = document.getElementById("mcp-servers");
  const note = document.getElementById("mcp-note");
  const path = document.getElementById("mcp-config-path");
  if (path) path.textContent = data.configPath || "（未知）";

  if (!data.servers || !data.servers.length) {
    box.innerHTML =
      '<div class="cache-empty">还没有配置 MCP 服务器。在数据目录新建 mcp.json 后点「重新连接」。</div>';
    note.textContent = data.errors && data.errors.length
      ? `配置有 ${data.errors.length} 处问题：${data.errors.join("；")}`
      : (data.loaded ? "未发现 mcpServers 配置" : "后台装载中…");
    return;
  }

  box.innerHTML = data.servers.map((server) => {
    const state = server.connected ? "已连接" : (server.enabled ? "连接失败" : "已禁用");
    const badge = server.connected ? "badge-lime" : (server.enabled ? "badge-red" : "badge-teal");
    const version = [server.serverName, server.serverVersion].filter(Boolean).join(" ");
    const protocol = server.protocolVersion ? `MCP ${server.protocolVersion}` : "MCP —";
    const tools = (server.toolNames || []).slice(0, 6).join("、");
    const details = [
      esc(server.command || ""),
      `${Number(server.toolCount || 0)} 个工具`,
      `risk=${esc(server.risk || "write")}`,
      protocol,
      version ? esc(version) : "",
      Number(server.restarts || 0) ? `已重连 ${Number(server.restarts)} 次` : "",
    ].filter(Boolean).join(" · ");
    return `
      <div class="mcp-row">
        <span class="mcp-dot ${server.connected ? "on" : "off"}"></span>
        <div class="mcp-main">
          <b>${esc(server.name)}</b>
          <small>${details}</small>
          ${tools ? `<small class="mcp-tools" title="前 6 个工具">${esc(tools)}${(server.toolNames || []).length > 6 ? " …" : ""}</small>` : ""}
          ${server.error ? `<small class="mcp-error">${esc(server.error)}</small>` : ""}
        </div>
        <span class="badge ${badge}">${state}</span>
        ${server.lazy && !server.connected
          ? `<button class="btn-ghost btn-sm" type="button" data-mcp-activate="${esc(server.name)}"
                     title="延迟加载：点一下才连接并注册工具">加载</button>`
          : `<button class="btn-ghost btn-sm" type="button" data-mcp-reconnect="${esc(server.name)}"
                     ${server.enabled ? "" : "disabled"}
                     title="${server.enabled ? "只重连这个 server" : "server 在配置里已禁用"}">重连</button>`}
      </div>`;
  }).join("");

  const errors = data.errors || [];
  note.textContent = errors.length
    ? `配置有 ${errors.length} 处问题：${errors.join("；")}`
    : `共 ${data.tools || 0} 个 MCP 工具已进入工具面（健康 ${data.healthy ?? 0} / 失败 ${data.failing ?? 0}；每 ${data.monitorIntervalS ?? 30} 秒巡检）`;
}

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}
