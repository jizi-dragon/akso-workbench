/** 审批弹窗：**需要用户拍板的决定一律走模态，不走对话流里的卡片**。
 *
 *   ## 为什么改（2026-09-20，用户反馈）
 *
 *   > 「提供给用户审批的，应该设计为弹出窗，而不是放在对话框中随着输出消失，
 *   >  理想中的行为应该是 agent 暂停 - 弹出确认窗口 - 得到反馈 - 注入上下文 -
 *   >  根据用户的回答判断下一步。」
 *
 *   内联卡片有三个绕不过去的问题：
 *   1. **会滚走**——模型继续说两句话，卡片就出了视口，而这是一次**真实写入的闸门**；
 *   2. **不要求答复**——用户可以继续打字、切页面，那条待确认就一直挂着；
 *   3. **不显眼**——它长得像一条消息，而它不是消息。
 *
 *   模态把这三件事一次解决：它盖住界面、必须被处理、且带倒计时（超时按未同意处理）。
 *
 *   ## 三态答复，而不是两态
 *
 *   | 按钮 | 语义 | 对循环 |
 *   |---|---|---|
 *   | 同意 | 明确同意 | 唤醒循环继续 |
 *   | 拒绝 | 明确拒绝（可填理由，理由会回给模型） | 唤醒循环，模型看到拒绝 |
 *   | 稍后（Esc 同） | **没答复** | 收成底部待办条，循环继续等；到点仍算未同意 |
 *
 *   「稍后」是刻意加的第三态：只给"同意/拒绝"两态时，用户想先看一眼对话再决定
 *   就只剩"关掉弹窗"这一条路，而关掉等于**悄悄丢掉一次确认**。
 *   收成待办条既保住了"它还在"，也没替用户作答。
 *
 *   ## 与风险门那张卡的分工（别读混）
 *
 *   - `confirmation`（模型主动问）：弹窗出现时**循环正卡在等待里**，点完模型自己继续。
 *   - `approval`（宿主风险门）：弹窗出现时**本轮已经结束**，放行后要让模型重试同一次调用。
 *
 *   两者的标题与说明文案因此不同，`kind` 字段就是为这件事存在的。
 *
 *   ## 安全基线
 *
 *   与 `chat-tools.js` 同源：一切动态内容走 `textContent`，**绝不 innerHTML 拼接**；
 *   令牌是一次性凭证，**不回填 DOM、不落日志**。
 */

import { api } from "./api.js";

/** 单条待办条的最小信息量：够标题一行 + 重新打开 */
const pending = new Map(); // pendingId → request
let queue = []; // 等待展示的请求
let current = null; // 当前展示的
let dialog = null;
let strip = null;

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined && text !== null) el.textContent = String(text);
  return el;
}

const RISK_LABEL = { read: "只读", write: "写入", dangerous: "危险" };

function riskLabel(risk) {
  const key = String(risk || "");
  return RISK_LABEL[key] || `未知风险（${key || "未标注"}）`;
}

function riskBadge(risk) {
  const key = String(risk || "");
  const badge = node("span", "badge tool-risk");
  if (key === "write") badge.classList.add("badge-amber");
  if (key === "dangerous") badge.classList.add("badge-red");
  badge.dataset.risk = key;
  badge.textContent = riskLabel(key);
  return badge;
}

function formatSeconds(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  if (value < 60) return `${value} 秒`;
  return `${Math.floor(value / 60)} 分 ${String(value % 60).padStart(2, "0")} 秒`;
}

function prettyArgs(raw) {
  const text = String(raw ?? "").trim();
  if (!text) return "（无参数）";
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

/* ———————————————— 拒绝理由（原生 dialog：焦点与 Esc 交给 UA） ———————————————— */

let rejectUi = null;

function ensureRejectDialog() {
  if (rejectUi) return rejectUi;

  const box = node("dialog", "tk-dialog");
  const card = node("div", "tk-dialog-card");
  card.appendChild(node("h3", "tk-dialog-title", "拒绝这次操作"));
  const target = node("p", "tk-dialog-target");
  card.appendChild(target);

  const field = node("label", "tk-field");
  field.appendChild(node("span", "tk-field-label", "拒绝理由（可选）· 理由会回给模型，让它别再试"));
  const textarea = node("textarea", "tk-dialog-reason");
  textarea.rows = 3;
  textarea.placeholder = "例如：这次只做演练，不要动真实平台";
  field.appendChild(textarea);
  card.appendChild(field);

  const actions = node("div", "tk-dialog-actions");
  const cancel = node("button", "btn-ghost", "取消");
  cancel.type = "button";
  const confirm = node("button", "btn-primary", "确定拒绝");
  confirm.type = "button";
  actions.appendChild(cancel);
  actions.appendChild(confirm);
  card.appendChild(actions);
  box.appendChild(card);
  document.body.appendChild(box);

  const ui = { dialog: box, target, textarea, resolve: null };
  const finish = (value) => {
    const resolve = ui.resolve;
    ui.resolve = null;
    if (box.open) box.close();
    if (resolve) resolve(value);
  };
  confirm.addEventListener("click", () => finish(ui.textarea.value.trim()));
  cancel.addEventListener("click", () => finish(null));
  box.addEventListener("close", () => finish(null)); // Esc 走原生 cancel→close，同样按「取消」

  rejectUi = ui;
  return ui;
}

function askRejectReason(targetText) {
  const ui = ensureRejectDialog();
  return new Promise((resolve) => {
    if (ui.dialog.open) {
      // 已有一次在问：两个 Promise 会互相覆盖 resolve，先到的那次就永远不落地
      resolve(null);
      return;
    }
    ui.target.textContent = targetText;
    ui.textarea.value = "";
    ui.resolve = resolve;
    ui.dialog.showModal();
    ui.textarea.focus();
  });
}

/* ———————————————— 弹窗本体 ———————————————— */

function buildDialog() {
  const box = node("dialog", "tk-dialog tk-decision");
  box.setAttribute("aria-labelledby", "tk-decision-title");
  // 让弹窗自己可聚焦：`showModal()` 默认会把焦点给第一个可聚焦后代，
  // 而那是「稍后」还是「同意」由 DOM 顺序决定——**不能赌**。
  // 焦点落在弹窗容器上时，一个顺手的 Enter 不会等于同意一次真实写入。
  box.tabIndex = -1;
  const card = node("div", "tk-dialog-card tk-decision-card");

  const head = node("div", "tk-decision-head");
  const title = node("h3", "tk-dialog-title");
  title.id = "tk-decision-title";
  head.appendChild(title);
  const badges = node("div", "tk-decision-badges");
  head.appendChild(badges);
  card.appendChild(head);

  const question = node("div", "tk-decision-question");
  card.appendChild(question);
  const details = node("div", "tk-decision-details");
  card.appendChild(details);

  const targetBlock = node("div", "tk-decision-target");
  targetBlock.hidden = true;
  targetBlock.appendChild(node("div", "tool-section-label", "将要执行的调用（同意后按这些参数执行）"));
  const argsEl = node("pre", "tool-args");
  targetBlock.appendChild(argsEl);
  card.appendChild(targetBlock);

  const meta = node("div", "tk-decision-meta");
  card.appendChild(meta);

  const actions = node("div", "tk-dialog-actions tk-decision-actions");
  const later = node("button", "btn-ghost", "稍后");
  later.type = "button";
  later.title = "先收起来（不算答复）——它不会消失，会停在输入框上方";
  const deny = node("button", "btn-ghost", "拒绝");
  deny.type = "button";
  const allow = node("button", "btn-primary", "同意");
  allow.type = "button";
  actions.appendChild(later);
  actions.appendChild(deny);
  actions.appendChild(allow);
  card.appendChild(actions);

  const state = node("div", "tk-decision-state");
  state.hidden = true;
  card.appendChild(state);

  box.appendChild(card);
  document.body.appendChild(box);

  const ui = { box, title, badges, question, details, targetBlock, argsEl, meta, actions, state,
               later, deny, allow };
  return ui;
}

function ensureDialog() {
  if (dialog) return dialog;
  dialog = buildDialog();

  // Esc **不当作答复**：它等于「稍后」——收成待办条，循环继续等。
  // 不 preventDefault 的话，原生 dialog 会把 Esc 当关闭，而关闭在用户眼里
  // 和"拒绝"长得一样，代价太大。
  dialog.box.addEventListener("cancel", (event) => {
    event.preventDefault();
    deferCurrent();
  });
  // 点遮罩不关闭：这是要人拍板的闸门，误点一下不该把它收走
  dialog.box.addEventListener("click", (event) => {
    if (event.target === dialog.box) event.stopPropagation();
  });

  dialog.later.addEventListener("click", deferCurrent);
  dialog.deny.addEventListener("click", () => void decide(false));
  dialog.allow.addEventListener("click", () => void decide(true));
  return dialog;
}

let countdownTimer = 0;

function stopCountdown() {
  if (countdownTimer) {
    clearInterval(countdownTimer);
    countdownTimer = 0;
  }
}

function renderCountdown(request) {
  const total = Number(request.timeoutS) || 0;
  if (total <= 0) return;
  const deadline = Date.now() + total * 1000;
  const paint = () => {
    const left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
    dialog.meta.textContent = left > 0
      ? `最多等待 ${formatSeconds(left)}；超时按「未同意」处理`
      : "已超过等待时间——服务端会按「未同意」处理，这条确认随即失效";
    if (left <= 0) stopCountdown();
  };
  paint();
  stopCountdown();
  countdownTimer = setInterval(paint, 1000);
}

function show(request) {
  const ui = ensureDialog();
  current = request;

  const isConfirmation = request.kind !== "approval";
  ui.title.textContent = isConfirmation ? "模型请求确认" : "需要你放行";
  ui.state.hidden = true;
  ui.state.textContent = "";
  ui.actions.hidden = false;
  ui.allow.disabled = false;
  ui.deny.disabled = false;
  ui.later.disabled = false;

  ui.badges.replaceChildren();
  if (request.risk) ui.badges.appendChild(riskBadge(request.risk));
  if (request.tool) ui.badges.appendChild(node("span", "tk-decision-tool mono", request.tool));

  ui.question.textContent = isConfirmation
    ? (request.question || "（模型没有说明要确认什么）")
    : (request.summary || "（平台未给出这次调用的说明）");

  ui.details.replaceChildren();
  if (isConfirmation && request.details) {
    ui.details.textContent = request.details;
    ui.details.hidden = false;
  } else if (!isConfirmation) {
    // 风险门的说明必须点破"点完之后还要做什么"，否则用户会以为模型会自己接着跑
    ui.details.textContent = "放行不会自动重发这次调用——本轮对话已经结束。"
      + "放行后在输入框里让模型重试同一次调用（参数要一样）即可执行；"
      + "换了参数需要重新确认。";
    ui.details.hidden = false;
  } else {
    ui.details.hidden = true;
  }

  const target = isConfirmation ? request.target : null;
  const argsJson = target && target.args && Object.keys(target.args).length
    ? JSON.stringify(target.args)
    : (request.args ? JSON.stringify(request.args) : "");
  if (argsJson) {
    ui.argsEl.textContent = prettyArgs(argsJson);
    ui.targetBlock.hidden = false;
  } else {
    ui.targetBlock.hidden = true;
  }

  renderCountdown(request);
  ui.box.showModal();
  // 焦点给弹窗本身而不是「同意」：焦点落在主按钮上时，一个顺手的 Enter
  // 就等于同意了一次真实写入
  ui.box.focus();
}

function advance() {
  current = null;
  stopCountdown();
  if (dialog && dialog.box.open) dialog.box.close();
  const next = queue.shift();
  if (next) show(next);
  paintStrip();
}

function deferCurrent() {
  // 「稍后」= 没答复：条目留在待办条里（`advance()` 最后会重画它），
  // 弹窗让位给下一条。刻意**不**在这里先 paintStrip——那时 `current` 还是它，
  // 会被"排除当前这条"的过滤规则滤掉，于是待办条上反而看不到它。
  advance();
}

async function decide(approved) {
  if (!current) return;
  const request = current;
  const ui = dialog;
  let reason = "";
  if (!approved) {
    reason = await askRejectReason(
      `${request.tool || "这次操作"}${request.risk ? `（${riskLabel(request.risk)}）` : ""}`);
    if (reason === null) return; // 取消：什么都没发生，弹窗保持原样
  }

  ui.allow.disabled = true;
  ui.deny.disabled = true;
  ui.later.disabled = true;
  ui.state.hidden = false;
  ui.state.className = "tk-decision-state is-pending";
  ui.state.textContent = approved ? "正在同意…" : "正在拒绝…";

  try {
    if (approved) await api.approvePending(request.pendingId);
    else await api.rejectPending(request.pendingId, reason);
    // 令牌是一次性凭证，**不回填 DOM**。确认门那条路循环本来就不需要它：
    // 服务端按「工具 + 参数指纹」认领这次批准。
    ui.state.className = `tk-decision-state is-${approved ? "allowed" : "rejected"}`;
    ui.state.textContent = approved
      ? (request.kind === "approval"
        ? "已放行 · 现在让模型重试同一次调用即可执行"
        : "已同意 · 模型继续")
      : "已拒绝 · 模型不会执行";
    pending.delete(request.pendingId);
    // 重入保护：SSE 的 `confirm_resolved` 可能在这 900ms 内也收口一次并同样
    // 排队 advance；两次都放行会把**下一条**待确认直接跳过去。
    setTimeout(() => {
      if (current && current.pendingId === request.pendingId) advance();
    }, 900);
  } catch (error) {
    if (error.status === 409) {
      // 已决策/已过期：这不是失败，是"来晚了"——如实说，然后收掉
      ui.state.className = "tk-decision-state is-stale";
      ui.state.textContent = `已失效／已决策：${error.message}`;
      pending.delete(request.pendingId);
      setTimeout(() => {
        if (current && current.pendingId === request.pendingId) advance();
      }, 1400);
    } else {
      ui.allow.disabled = false;
      ui.deny.disabled = false;
      ui.later.disabled = false;
      ui.state.className = "tk-decision-state is-failed";
      ui.state.textContent = `提交失败：${error.message}`;
    }
  }
}

/* ———————————————— 待办条（「稍后」的落点） ———————————————— */

function ensureStrip() {
  if (strip) return strip;
  strip = node("div", "pending-strip");
  strip.hidden = true;
  const wrap = document.querySelector(".composer-wrap") || document.body;
  wrap.insertBefore(strip, wrap.firstChild);
  return strip;
}

function paintStrip() {
  const bar = ensureStrip();
  const items = [...pending.values()].filter((r) => r.pendingId !== (current || {}).pendingId);
  if (!items.length) {
    bar.hidden = true;
    bar.replaceChildren();
    return;
  }
  bar.hidden = false;
  const label = node("span", "pending-strip-label",
    items.length === 1 ? "有 1 件事等你拍板" : `有 ${items.length} 件事等你拍板`);
  bar.replaceChildren(label);
  for (const request of items) {
    const button = node("button", "pending-strip-item");
    button.type = "button";
    button.textContent = request.question || request.summary || request.tool || "待确认";
    button.title = "点开这条待确认";
    button.addEventListener("click", () => {
      queue = queue.filter((r) => r.pendingId !== request.pendingId);
      queue.unshift(request);
      if (!current) advance();
      else show(request);
    });
    bar.appendChild(button);
  }
}

/* ———————————————— 对外接口 ———————————————— */

/** 请求一次决定：`request` 形如
 *  `{pendingId, kind, tool, risk, question?, details?, timeoutS?, target?, summary?, args?}` */
export function requestDecision(request) {
  if (!request || !request.pendingId) return;
  if (pending.has(request.pendingId)) return; // 重放（SSE 重连）不重复弹
  pending.set(request.pendingId, request);
  if (current) queue.push(request);
  else show(request);
  paintStrip();
}

/** 服务端给出了权威答复（`confirm_resolved`）→ 收掉弹窗/待办条。
 *
 *  ⚠️ 权威在服务端：用户点按钮只是**提交**，终态以这条事件为准。
 *  所以这里连 `decide()` 已经收过的条目也要处理一遍（幂等）。
 */
export function settleDecision(pendingId, verdict = {}) {
  const id = String(pendingId || "");
  if (!id) return;
  pending.delete(id);
  queue = queue.filter((r) => r.pendingId !== id);
  if (current && current.pendingId === id) {
    const ui = ensureDialog();
    ui.allow.disabled = true;
    ui.deny.disabled = true;
    ui.later.disabled = true;
    ui.state.hidden = false;
    if (verdict.approved) {
      ui.state.className = "tk-decision-state is-allowed";
      ui.state.textContent = current.kind === "approval"
        ? "已放行 · 现在让模型重试同一次调用即可执行"
        : "已同意 · 模型继续";
    } else if (verdict.expired) {
      ui.state.className = "tk-decision-state is-stale";
      ui.state.textContent = "超时未答复 · 已按未同意处理（没有产生任何批准）";
    } else {
      ui.state.className = "tk-decision-state is-rejected";
      ui.state.textContent = `已拒绝 · 模型不会执行${verdict.answer ? `：${verdict.answer}` : ""}`;
    }
    setTimeout(() => {
      if (current && current.pendingId === id) advance();
    }, 1200);
  }
  paintStrip();
}

/** 页面加载时认领**遗留的**待确认。
 *
 *  没有这一步时，一条待确认只活在当前这条 SSE 流里：刷新页面、切走再看、
 *  或者干脆没注意到，它就永远没人处理了——而它是一个**真实写入的闸门**。
 *  服务端本来就留着权威状态（`GET /api/agent/pending`），这里只是把它接上界面。
 */
export async function recoverPending() {
  try {
    const data = await api.listPending();
    for (const row of data.pending || []) {
      requestDecision({
        pendingId: row.id,
        kind: row.kind || "approval",
        tool: row.tool,
        risk: row.risk,
        summary: row.summary,
        args: row.args,
        question: row.kind === "confirmation" ? row.summary : undefined,
        recovered: true,
      });
    }
  } catch {
    // 认领失败不该影响对话本身：它只是一次补偿
  }
}

export function initApprovalModal() {
  ensureDialog();
  ensureStrip();
}
