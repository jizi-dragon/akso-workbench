/**
 * 「本次运行」时间线（计划 §6.2）。
 *
 * ## 它为什么存在
 *
 * §6.1 把 `agent_run` / `agent_span` 落了库，而**在它之前没有任何界面读它们** ——
 * 数据在库里、只有 `sqlite3` 命令行看得见。★ 这正是 PITFALLS #106 的形态：
 * **写了一半的可观测性不是可观测性**（"落了库"与"能被看见"是两件事，
 * 而前者不会因为后者缺失而报错）。
 *
 * ## 三个刻意的显示口径
 *
 * ① ★ **摘要那次调用单独标红**（`attrs.summary`，由产生它的地方标）。
 *    它是**几乎必然全 miss** 的开销 —— 摘要要读完整段历史，前缀命中的可能性最低，
 *    而它按 miss 的价（命中价的 50 倍）计费。不标出来的话，用户看到"这一轮贵"
 *    只会去怪工具调用。
 *
 * ② **缓存命中按 span 显示，不按 run 平均**。平均会把"摘要全 miss"与
 *    "三次工具轮全命中"抹成中间值，而那个值**指不出该改哪里**。
 *
 * ③ **没有运行记录时说明"为什么没有"**，不是显示一个空面板。
 *    空面板与"接口坏了"在界面上长得一样（同族：`/api/agent/runs` 的空结果返回 200 而不是 404，
 *    见那里的文档串）。
 */

import { api } from "./api.js";
import { state } from "./app.js";

const KIND_LABEL = {
  model: "模型",
  tool: "工具",
  sandbox: "沙箱",
  confirm: "确认",
};

/**
 * 状态 → 徽章文案与色调。
 *
 * ★ 色板类名只有 `badge-teal` / `badge-lime` / `badge-amber` / `badge-red` / `badge-sky`
 * （`base.css`），**没有** `badge-ok` 这类语义名 —— 我第一版凭印象编了几个不存在的类名，
 * 后果是徽章退化成 `.badge` 基类（灰底灰字），而**界面上看不出哪里错了**：
 * 它只是"颜色不太对"，不是"少了一个元素"。⇒ 判据在 `verify_agent_ui.py` 里
 * 按**计算样式**核（同 PITFALLS #87 的教训：字符串断言看不出"渲染出来是什么样"）。
 *
 * **未知状态也要能显示出来**（回落 `.badge` 基类 + 原样文案）——
 * 显示成空白的话，一个"我们没见过的失败状态"看起来就像"没问题"。
 */
const STATUS_TONE = {
  ok: ["badge-lime", "完成"],
  error: ["badge-red", "失败"],
  truncated: ["badge-red", "被截断"],
  running: ["badge-amber", "进行中"],
  aborted: ["badge-sky", "已中止"],
  failed: ["badge-red", "失败"],
  succeeded: ["badge-lime", "成功"],
};

function tone(status) {
  return STATUS_TONE[status] || ["", status || "未知"];
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}

function fmtMs(ms) {
  const n = Number(ms) || 0;
  return n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${n}ms`;
}

function fmtTime(ms) {
  if (!ms) return "";
  const d = new Date(Number(ms));
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}

/** 这一步的缓存命中率 —— 只在**有 usage 的时候**给，没有就返回 null（不编一个 0%）。 */
function cachePct(usage) {
  if (!usage || typeof usage !== "object") return null;
  const hit = Number(usage.prompt_cache_hit_tokens);
  const miss = Number(usage.prompt_cache_miss_tokens);
  if (!Number.isFinite(hit) && !Number.isFinite(miss)) return null;
  const total = (hit || 0) + (miss || 0);
  if (total <= 0) return null;
  return Math.round(((hit || 0) / total) * 100);
}

function spanRow(span) {
  const attrs = span.attrs || {};
  const row = el("div", "rt-span");
  // ★ 摘要那次调用：**加一个可见的类**，样式在 app.css 里标红
  if (attrs.summary) row.classList.add("rt-span-summary");

  row.appendChild(el("span", "rt-step", `#${span.step}`));
  row.appendChild(el("span", `rt-kind rt-kind-${span.spanKind}`, KIND_LABEL[span.spanKind] || span.spanKind));

  const name = el("span", "rt-name", span.name || "");
  row.appendChild(name);

  const [badgeClass, badgeText] = tone(span.status);
  row.appendChild(el("span", `badge ${badgeClass} rt-status`, badgeText));
  row.appendChild(el("span", "rt-dur", fmtMs(span.durationMs)));

  if (attrs.summary) {
    // ★ 说清"为什么标红"，而不是只标个颜色 —— 颜色本身不解释任何事
    const note = el("span", "rt-note", "摘要调用 · 几乎必然全 miss");
    row.appendChild(note);
  }

  const pct = cachePct(attrs.usage);
  if (pct != null) {
    row.appendChild(el("span", pct === 0 ? "rt-cache rt-cache-miss" : "rt-cache", `缓存 ${pct}%`));
  }

  if (span.error) row.appendChild(el("span", "rt-error", span.error));
  return row;
}

function runBlock(run) {
  const block = el("div", "rt-run");
  const head = el("div", "rt-run-head");

  const [badgeClass, badgeText] = tone(run.status);
  head.appendChild(el("span", `badge ${badgeClass}`, badgeText));
  head.appendChild(el("span", "rt-run-time", fmtTime(run.startedAt)));
  head.appendChild(el("span", "rt-run-meta", `${run.steps || 0} 步 · ${fmtMs(run.latencyMs)}`));

  const total = Number(run.promptTokens) || 0;
  const hit = Number(run.cacheHitTokens) || 0;
  if (total > 0) {
    head.appendChild(el("span", "rt-run-meta", `输入 ${total} · 命中 ${hit}（${Math.round((hit / total) * 100)}%）`));
  }
  if (Number(run.summaryCalls) > 0) {
    head.appendChild(el("span", "rt-run-meta rt-run-summary", `摘要 ${run.summaryCalls} 次`));
  }
  block.appendChild(head);

  if (run.error) block.appendChild(el("div", "rt-error", run.error));

  const spans = run.spans || [];
  if (!spans.length) {
    // ★ 说明"为什么是空的"，不是留一块空白
    block.appendChild(el("div", "rt-empty", "这次运行没有记录到步骤（轨迹写入失败时是空的，看下面的自检）"));
  } else {
    for (const span of spans) block.appendChild(spanRow(span));
  }
  return block;
}

function render(body, out) {
  body.innerHTML = "";
  const runs = out.runs || [];
  if (!runs.length) {
    body.appendChild(el("div", "rt-empty",
      "这个会话还没有运行记录。发一条消息之后再点这里 —— 面板显示的是**每一次模型回合**的步骤。"));
  } else {
    for (const run of runs) body.appendChild(runBlock(run));
  }
  if (Number(out.traceWriteFailures) > 0) {
    // ★ 轨迹写失败时**必须说出来**：否则面板会显得"这次运行只有两步"，
    // 而实际是中间那几步没写进去（PITFALLS #104 同族：静默丢失）
    body.appendChild(el("div", "rt-error",
      `有 ${out.traceWriteFailures} 条轨迹写入失败 —— 下面的时间线可能缺步骤`));
  }
}

export async function openRunTimeline() {
  const panel = document.getElementById("run-timeline");
  if (!panel) return;
  panel.hidden = false;
  const body = document.getElementById("run-timeline-body");
  body.innerHTML = "";
  body.appendChild(el("div", "rt-empty", "正在读取…"));
  try {
    const out = await api.getRuns(state.currentConversationId || "");
    render(body, out);
  } catch (err) {
    // ★ 失败与"没有记录"**必须显示成两件事** —— 合并会让一次真实故障看起来像"还没跑过"
    body.innerHTML = "";
    body.appendChild(el("div", "rt-error", `读取失败：${err.message}`));
  }
}

export function closeRunTimeline() {
  const panel = document.getElementById("run-timeline");
  if (panel) panel.hidden = true;
}

export function initRunTimeline() {
  document.getElementById("btn-run-timeline")?.addEventListener("click", () => {
    const panel = document.getElementById("run-timeline");
    if (panel && !panel.hidden) closeRunTimeline();
    else void openRunTimeline();
  });
  document.getElementById("btn-run-timeline-close")?.addEventListener("click", closeRunTimeline);
}
