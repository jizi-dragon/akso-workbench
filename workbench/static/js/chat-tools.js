/** 对话工具面：工具调用轨迹卡 / 人工确认卡 / 工具清单浮层
 *
 *  安全基线：工具名、参数、插件 id、结果摘要全部来自模型或远端平台，
 *  本文件一律用 textContent 落值、不用 innerHTML 拼动态内容——任何一处改成
 *  字符串拼接，都等于把远端平台的内容当本页代码执行。结果摘要是纯文本，
 *  绝不走 markdown.js（否则远端回一句 `<img onerror=…>` 就成了注入点）。
 */

import { api } from "./api.js";

/** 参数超过这个长度就默认折叠：一屏塞进几百行 JSON 会让人找不到状态行 */
const ARGS_CLAMP_CHARS = 200;

/** 风险徽章复用 base.css 的 .badge 语义色（中性/琥珀/红），不另造色板 */
const RISK_META = {
  read: { label: "只读", badge: "" },
  write: { label: "写入", badge: "badge-amber" },
  dangerous: { label: "危险", badge: "badge-red" },
};

/** 每种终态都有自己的文案与颜色：它们要人做的事完全不同——
 *  「参数不合法」等模型改参数、「未知工具」要人去查插件、「超时」可重试、
 *  「已拒绝」是人已经拍过板。全收敛成一个「错误」会把这些线索全丢掉。 */
const STATUS_META = {
  running: { cls: "running", glyph: "◌", label: "执行中", spin: true },
  ok: { cls: "done", glyph: "✓", label: "完成" },
  error: { cls: "error", glyph: "✕", label: "执行失败" },
  timeout: { cls: "timeout", glyph: "!", label: "执行超时" },
  unknown_tool: { cls: "unknown-tool", glyph: "?", label: "未知工具" },
  bad_args: { cls: "bad-args", glyph: "!", label: "参数不合法" },
  rejected: { cls: "rejected", glyph: "⊘", label: "已拒绝" },
  needs_approval: { cls: "needs-approval", glyph: "!", label: "待人工确认" },
};

/** 失败类状态默认展开卡片：收起的卡片长得和「什么都没发生」一样，
 *  而失败恰恰是唯一必须让人当场看见的状态 */
const EXPAND_STATES = new Set(["error", "timeout", "unknown_tool", "bad_args"]);

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined && text !== null) el.textContent = String(text);
  return el;
}

export function riskLabel(risk) {
  const meta = RISK_META[String(risk || "")];
  return meta ? meta.label : `未知风险（${String(risk || "未标注")}）`;
}

export function riskBadge(risk) {
  const key = String(risk || "");
  const meta = RISK_META[key];
  const badge = node("span", "badge tool-risk");
  if (meta && meta.badge) badge.classList.add(meta.badge);
  badge.dataset.risk = key;
  badge.textContent = meta ? meta.label : riskLabel(risk);
  return badge;
}

function formatDuration(ms) {
  const value = Number(ms);
  if (!Number.isFinite(value) || value <= 0) return "未知时长";
  if (value < 1000) return `${Math.round(value)} 毫秒`;
  if (value < 60000) return `${Math.round(value / 1000)} 秒`;
  return `${Math.round(value / 60000)} 分钟`;
}

function prettyArgs(raw) {
  const text = String(raw ?? "").trim();
  if (!text) return "（无参数）";
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    // 模型偶尔给出半截或非 JSON 的 arguments：原样展示。
    // 解析失败就丢弃它，会让人对着「（无参数）」查一个参数其实存在的调用
    return text;
  }
}

/** 结果状态归一：优先用后端给的 status，缺失时用 ok/isError 兜底，
 *  完全认不出的状态原样显示——猜成 ok 比显示「未知状态」危险得多 */
function resolveState(result, invocation) {
  const raw = String((result && result.status) || "");
  if (STATUS_META[raw]) return raw;
  if ((result && result.ok === false) || invocation.isError) return "error";
  if (raw) return raw;
  return result && result.ok === true ? "ok" : "error";
}

function setStatus(handle, state, durationMs) {
  const meta = STATUS_META[state] || { cls: "unknown", glyph: "?", label: state || "未知状态" };
  handle.card.dataset.state = state;
  handle.statusEl.className = `tool-status ${meta.cls}`;
  handle.statusEl.dataset.status = state;
  handle.statusGlyphEl.className = meta.spin ? "tool-status-glyph spin" : "tool-status-glyph";
  handle.statusGlyphEl.textContent = meta.glyph;
  const ms = Number(durationMs);
  const withTime = !meta.spin && Number.isFinite(ms) && ms >= 0;
  handle.statusTextEl.textContent = withTime ? `${meta.label} · ${ms} ms` : meta.label;
}

/* ———————————————— 工具调用轨迹 ———————————————— */

/** 轨迹容器：卡片按发生顺序落进「当前这一块」。
 *  文字重新开始时调用 endRound()，下一批卡片另起一块——
 *  这样 DOM 顺序 = 真实发生顺序，而不是把所有卡片堆到气泡底下。 */
export function createToolTrail(host) {
  let block = null; // 当前轨迹块（一轮工具调用的卡片容器）
  let pendingThought = null; // 已到达但还没等到工具卡的模型旁白
  let lastHandle = null; // 最近一张卡：observation 缺 auditId 时的兜底目标
  const cards = new Map(); // auditId → 卡片句柄
  const confirms = new Map(); // pendingId → 确认卡句柄（等 `confirm_resolved` 来收口）

  const openBlock = () => {
    if (block) return block;
    block = node("div", "tool-trail");
    block.appendChild(node("div", "tool-trail-label", "审计轨迹 · 工具调用"));
    if (pendingThought) {
      // 旁白只在真有工具调用时才有意义：单独一段叙述属于正文气泡，不该重复出现在这里
      const thought = node("div", "tool-thought");
      thought.appendChild(node("span", "tool-thought-label", "旁白"));
      thought.appendChild(node("span", "tool-thought-text", pendingThought));
      pendingThought = null;
      block.appendChild(thought);
    }
    host.appendChild(block);
    return block;
  };

  const addAction = (event) => {
    const call = event.call || {};
    const tool = event.tool || {};
    const auditId = String(event.auditId || "");

    const card = node("div", "tool-card");
    card.dataset.state = "running";
    if (auditId) card.dataset.auditId = auditId;

    // 头部用真 <button>：键盘 Tab/Enter 与 aria-expanded 由 UA 负责，
    // 用 div + click 手搓焦点管理，键盘用户就永远打不开这张卡
    const header = node("button", "tool-card-header");
    header.type = "button";
    header.setAttribute("aria-expanded", "false");
    const caret = node("span", "tool-caret", "▸");
    header.appendChild(caret);
    header.appendChild(node("span", "tool-name", call.name || tool.name || "未命名工具"));
    header.appendChild(riskBadge(tool.risk));
    if (tool.plugin) header.appendChild(node("span", "tool-plugin", tool.plugin));
    if (tool.known === false) {
      const unknown = node("span", "tool-known-chip", "未登记");
      unknown.title = "该名字不在 /api/agent/tools 目录里（多半是模型拼错了）";
      header.appendChild(unknown);
    }
    const statusEl = node("span", "tool-status running");
    const statusGlyphEl = node("span", "tool-status-glyph", "◌");
    const statusTextEl = node("span", "tool-status-text", "执行中");
    statusEl.appendChild(statusGlyphEl);
    statusEl.appendChild(statusTextEl);
    header.appendChild(statusEl);
    card.appendChild(header);

    const panel = node("div", "tool-card-panel");
    panel.hidden = true;

    const argsBlock = node("div", "tool-args-block");
    argsBlock.appendChild(node("div", "tool-section-label", "参数"));
    const pretty = prettyArgs(call.arguments);
    const pre = node("pre", "tool-args", pretty);
    const clamped = pretty.length > ARGS_CLAMP_CHARS;
    if (clamped) pre.classList.add("is-clamped");
    argsBlock.appendChild(pre);
    if (clamped) {
      const more = node("button", "tool-args-more", "展开全部参数");
      more.type = "button";
      more.addEventListener("click", () => {
        const folded = pre.classList.toggle("is-clamped");
        more.textContent = folded ? "展开全部参数" : "收起参数";
      });
      argsBlock.appendChild(more);
    }
    panel.appendChild(argsBlock);

    const resultBlock = node("div", "tool-result-block");
    resultBlock.hidden = true;
    resultBlock.appendChild(node("div", "tool-section-label", "结果"));
    const summaryEl = node("div", "tool-result-summary");
    resultBlock.appendChild(summaryEl);
    panel.appendChild(resultBlock);

    if (auditId) panel.appendChild(node("div", "tool-audit", "审计 ID " + auditId));
    card.appendChild(panel);

    const setOpen = (open) => {
      panel.hidden = !open;
      header.setAttribute("aria-expanded", open ? "true" : "false");
      caret.textContent = open ? "▾" : "▸";
    };
    header.addEventListener("click", () => setOpen(panel.hidden));

    const handle = { card, panel, statusEl, statusGlyphEl, statusTextEl, summaryEl, resultBlock, setOpen };
    if (auditId) cards.set(auditId, handle);
    lastHandle = handle;
    openBlock().appendChild(card);
    return handle;
  };

  const applyObservation = (event) => {
    const invocation = event.invocation || {};
    const result = event.result || {};
    const auditId = String(invocation.auditId || "");
    // 优先按 auditId 配对（同轮可以并发多次调用）；没带 auditId 的观测只可能属于
    // 最近一张卡，退回 lastHandle，总比新开一张对不上号的卡好
    let handle = auditId ? cards.get(auditId) : lastHandle;
    if (!handle) {
      // 带上 auditId 却找不到对应 action（乱序/重放）：凭 invocation 补一张卡，
      // 审计轨迹缺一条就很难再对上账
      handle = addAction({
        call: { name: invocation.tool || "", arguments: "" },
        tool: { name: invocation.tool, risk: invocation.risk, plugin: invocation.plugin, known: true },
        auditId,
      });
    }

    const state = resolveState(result, invocation);
    setStatus(handle, state, invocation.durationMs);
    handle.resultBlock.hidden = false;
    handle.summaryEl.textContent = String(result.summary ?? "");
    handle.summaryEl.classList.toggle("is-error", EXPAND_STATES.has(state));
    if (EXPAND_STATES.has(state) || state === "needs_approval") handle.setOpen(true);
    return handle;
  };

  return {
    /** 模型旁白：先存下，等这张卡真出现时再随轨迹块一起落位 */
    addThought(text) {
      pendingThought = String(text ?? "").trim() || null;
    },
    addAction,
    applyObservation,
    addApproval(event) {
      openBlock().appendChild(approvalCard(event));
    },
    /** 模型请求实时确认：对话循环此刻**正卡在等待里**，用户点完它自己就继续 */
    addConfirm(event) {
      const ui = confirmationCard(event);
      const pendingId = String(event.pendingId || "");
      if (pendingId) confirms.set(pendingId, ui);
      openBlock().appendChild(ui.card);
    },
    /** 服务端给出的**权威答复**（用户的点击只是提交，答复以这条事件为准）。 */
    settleConfirm(event) {
      const pendingId = String(event.pendingId || "");
      const ui = confirms.get(pendingId);
      if (!ui) return; // 卡不在（SSE 断线重连/换会话）：紧跟其后的 observation 已经说明结果
      confirms.delete(pendingId);
      if (ui.decided) return; // 本地已经收口（409 等），不覆盖
      if (event.approved) {
        ui.settle("allowed", "已同意 · 模型继续执行",
          "模型现在可以直接调用上面那次调用，参数一致即可，不会再问第二遍。");
      } else if (event.expired) {
        ui.settle("timeout", "超时未答复 · 已按未同意处理",
          "没有产生任何批准；如果需要，让模型重新发起一次确认。");
      } else {
        ui.settle("rejected", "已拒绝 · 模型不会执行",
          String(event.answer || "").trim() || "模型会看到这次拒绝，并被要求不要重试。");
      }
    },
    /** 文字段开始 = 上一轮工具调用结束，卡片从此另起一块 */
    endRound() {
      block = null;
    },
  };
}

/* ———————————————— 人工确认（危险操作） ———————————————— */

/** 拒绝理由对话框：Electron 里 window.prompt 不可用（accounts.html 已踩过这个坑），
 *  所以用原生 <dialog>——焦点陷阱、Esc 关闭、backdrop 都由 UA 负责。
 *  整个应用只需要一个实例，复用即可。 */
let rejectUi = null;

function ensureRejectDialog() {
  if (rejectUi) return rejectUi;

  const dialog = node("dialog", "tk-dialog");
  const card = node("div", "tk-dialog-card");
  card.appendChild(node("h3", "tk-dialog-title", "拒绝这次工具调用"));
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
  dialog.appendChild(card);
  document.body.appendChild(dialog);

  const ui = { dialog, target, textarea, resolve: null };
  const finish = (value) => {
    const resolve = ui.resolve;
    ui.resolve = null;
    if (dialog.open) dialog.close();
    if (resolve) resolve(value);
  };
  confirm.addEventListener("click", () => finish(ui.textarea.value.trim()));
  cancel.addEventListener("click", () => finish(null));
  dialog.addEventListener("close", () => finish(null)); // Esc 走原生 cancel→close，同样按「取消」处理

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

function approvalCard(event) {
  const pendingId = String(event.pendingId || "");
  const toolName = String(event.tool || "未知工具");

  const card = node("div", "approval-card");
  card.dataset.pendingId = pendingId;

  const head = node("div", "approval-head");
  head.appendChild(node("span", "approval-title", "需要人工确认"));
  head.appendChild(riskBadge(event.risk));
  head.appendChild(node("span", "approval-tool mono", toolName));
  card.appendChild(head);

  card.appendChild(node("div", "approval-summary", event.summary || "（平台未给出这次调用的说明）"));

  if (event.auditId) {
    const foot = node("div", "approval-foot", "审计 ID " + String(event.auditId));
    foot.title = "可在 GET /api/agent/audit 里按此 ID 查到这次调用";
    card.appendChild(foot);
  }

  const actions = node("div", "approval-actions");
  const allow = node("button", "btn-primary approval-allow", "放行");
  allow.type = "button";
  const deny = node("button", "btn-ghost approval-deny", "拒绝");
  deny.type = "button";
  actions.appendChild(allow);
  actions.appendChild(deny);
  card.appendChild(actions);

  const stateEl = node("div", "approval-state");
  stateEl.hidden = true;
  card.appendChild(stateEl);

  let decided = false;

  const setBusy = (busy) => {
    allow.disabled = busy;
    deny.disabled = busy;
    if (busy) {
      stateEl.hidden = false;
      stateEl.className = "approval-state is-pending";
      stateEl.textContent = busy === "allow" ? "正在放行…" : "正在拒绝…";
    }
  };

  /** 终局：按钮不留在页面上——已决策的操作再点一次只会拿到 409，
   *  留着按钮等于邀请人去撞那个 409 */
  const settle = (kind, text, detail) => {
    decided = true;
    actions.hidden = true;
    card.dataset.decision = kind;
    stateEl.hidden = false;
    stateEl.className = `approval-state is-${kind}`;
    stateEl.textContent = text;
    if (detail) stateEl.appendChild(node("div", "approval-detail", detail));
  };

  /** 非 409 的失败（网络/500）不是终局：按钮放回去，让人能重试 */
  const fail = (prefix, error) => {
    setBusy(false);
    stateEl.hidden = false;
    stateEl.className = "approval-state is-failed";
    stateEl.textContent = prefix + (error && error.message ? error.message : "未知原因");
  };

  allow.addEventListener("click", async () => {
    if (decided) return;
    setBusy("allow");
    try {
      const data = await api.approvePending(pendingId);
      // 令牌是一次性凭证：不回填 DOM、不落日志，避免被页面脚本或截图带走。
      // 也刻意不自动重发那次调用——放行是人做的决定，重发应当由人再发一条消息触发。
      // 但**重发确实能通过**：服务端按「工具 + 参数指纹」认领这次批准
      // （`PendingStore.claim_approved()`，2026-09-20 修 P0-A；此前重发永远被再拦一次）。
      settle(
        "allowed",
        "已放行",
        `这次调用不会自动重发——在输入框里让模型重试同一次调用（参数要一样）即可执行；${formatDuration(data.expiresInMs)}内有效，超时需重新确认。`,
      );
    } catch (error) {
      if (error.status === 409) settle("stale", "已失效／已决策", error.message);
      else fail("放行失败：", error);
    }
  });

  deny.addEventListener("click", async () => {
    if (decided) return;
    const reason = await askRejectReason(`${toolName}（${riskLabel(event.risk)}）`);
    if (reason === null) return; // 取消：什么都没发生，按钮保持原样
    setBusy("deny");
    try {
      await api.rejectPending(pendingId, reason);
      settle("rejected", "已拒绝", reason ? `理由：${reason}` : "未填写理由。模型会看到这次拒绝，并被要求不要再试。");
    } catch (error) {
      if (error.status === 409) settle("stale", "已失效／已决策", error.message);
      else fail("拒绝失败：", error);
    }
  });

  return card;
}

/* ———————————————— 模型请求确认（实时确认） ———————————————— */

/** `request_user_confirmation` 的确认卡。
 *
 *  **与 `approvalCard` 是两张卡，别合并**——它们要人做的事不一样：
 *
 *  | | 出现时机 | 用户点完之后 |
 *  |---|---|---|
 *  | `approvalCard`（风险门） | 本轮对话**已结束**，工具被拦下了 | 用户得再发一条消息让模型重试 |
 *  | 本卡（实时确认） | 对话循环**正卡在等待里** | 模型自己就继续了，不用再发消息 |
 *
 *  文案因此**不能互抄**：这里写「同意后我会继续」，风险门那张写「不会自动重发」。
 *  抄错方向的后果是让人对着一个已经复活的循环干等，或者反过来以为还要再发一句。
 */
function confirmationCard(event) {
  const pendingId = String(event.pendingId || "");
  const target = event.target || null;

  const card = node("div", "confirm-card");
  card.dataset.pendingId = pendingId;
  card.dataset.state = "asking";

  const head = node("div", "confirm-head");
  head.appendChild(node("span", "confirm-title", "模型请求确认"));
  if (target && target.tool) {
    head.appendChild(riskBadge(target.risk));
    head.appendChild(node("span", "confirm-tool mono", target.tool));
  }
  card.appendChild(head);

  card.appendChild(node("div", "confirm-question",
    event.question || "（模型没有说明要确认什么）"));
  if (event.details) card.appendChild(node("div", "confirm-details", event.details));

  if (target && target.tool) {
    const block = node("div", "confirm-target");
    block.appendChild(node("div", "tool-section-label",
      "同意后将要执行的调用（参数必须与这里完全一致才会被放行）"));
    const args = target.args && Object.keys(target.args).length
      ? prettyArgs(JSON.stringify(target.args))
      : "（无参数）";
    block.appendChild(node("pre", "tool-args", args));
    card.appendChild(block);
  } else {
    // 没绑定调用时必须说清楚：用户最容易误以为"点了同意就等于批准了后面的写操作"
    card.appendChild(node("div", "confirm-details",
      "这次确认没有绑定具体调用：同意只代表同意模型继续，不会放行任何写操作——"
      + "真正的写入仍会单独弹一次确认。"));
  }

  const timeoutS = Number(event.timeoutS);
  if (Number.isFinite(timeoutS) && timeoutS > 0) {
    card.appendChild(node("div", "confirm-timeout",
      `最多等待 ${formatDuration(timeoutS * 1000)}；超时按「未同意」处理`));
  }

  const actions = node("div", "confirm-actions");
  const allow = node("button", "btn-primary confirm-allow", "同意");
  allow.type = "button";
  const deny = node("button", "btn-ghost confirm-deny", "拒绝");
  deny.type = "button";
  actions.appendChild(allow);
  actions.appendChild(deny);
  card.appendChild(actions);

  const stateEl = node("div", "confirm-state");
  stateEl.hidden = true;
  card.appendChild(stateEl);

  const ui = { card, actions, stateEl, decided: false };
  ui.setBusy = (busy) => {
    allow.disabled = busy;
    deny.disabled = busy;
    if (busy) {
      stateEl.hidden = false;
      stateEl.className = "confirm-state is-pending";
      stateEl.textContent = busy === "allow"
        ? "已同意，正在唤醒模型…"
        : "正在提交拒绝…";
    }
  };
  /** 终局：按钮必须撤掉。已决策的确认再点一次只会拿到 409，
   *  留着按钮等于邀请人去撞那个 409（与风险门那张卡同一套理由）。 */
  ui.settle = (kind, text, detail) => {
    ui.decided = true;
    actions.hidden = true;
    card.dataset.state = kind;
    stateEl.hidden = false;
    stateEl.className = `confirm-state is-${kind}`;
    stateEl.textContent = text;
    if (detail) stateEl.appendChild(node("div", "approval-detail", detail));
  };
  /** 非 409 的失败不是终局：按钮放回去，让人能重试 */
  ui.fail = (prefix, error) => {
    ui.setBusy(false);
    stateEl.hidden = false;
    stateEl.className = "confirm-state is-failed";
    stateEl.textContent = prefix + (error && error.message ? error.message : "未知原因");
  };

  allow.addEventListener("click", async () => {
    if (ui.decided) return;
    ui.setBusy("allow");
    try {
      await api.approvePending(pendingId);
      // 令牌是一次性凭证，这里**不回填 DOM**。确认路径上循环本来就不需要它——
      // 服务端按「工具 + 参数指纹」认领这次批准，模型重放同一次调用即可。
      ui.setBusy("allow");
    } catch (error) {
      if (error.status === 409) ui.settle("stale", "已失效／已决策", error.message);
      else ui.fail("同意失败：", error);
    }
  });

  deny.addEventListener("click", async () => {
    if (ui.decided) return;
    const reason = await askRejectReason(`确认请求${target && target.tool ? `（${target.tool}）` : ""}`);
    if (reason === null) return; // 取消：什么都没发生，按钮保持原样
    ui.setBusy("deny");
    try {
      await api.rejectPending(pendingId, reason);
      ui.setBusy("deny");
    } catch (error) {
      if (error.status === 409) ui.settle("stale", "已失效／已决策", error.message);
      else ui.fail("拒绝失败：", error);
    }
  });

  return ui;
}

/* ———————————————— 工具清单浮层 ———————————————— */

let catalogCache = null; // 插件装配结果在进程生命周期内不变，取一次就够

function paletteError(message, onRetry) {
  const row = node("div", "tool-palette-msg is-error", message);
  const retry = node("button", "tool-palette-retry", "重试");
  retry.type = "button";
  retry.addEventListener("click", onRetry);
  row.appendChild(retry);
  return row;
}

export function initToolPalette(input) {
  const anchor = document.getElementById("tool-palette-anchor");
  const trigger = document.getElementById("btn-tool-palette");
  if (!anchor || !trigger || !input) return;

  const panel = node("div", "tool-palette");
  panel.id = "tool-palette";
  panel.hidden = true;
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "可用工具清单");
  const head = node("div", "tool-palette-head");
  head.appendChild(node("span", "tool-palette-title", "可用工具"));
  const closeBtn = node("button", "tool-palette-close", "×");
  closeBtn.type = "button";
  closeBtn.title = "关闭";
  head.appendChild(closeBtn);
  panel.appendChild(head);
  const body = node("div", "tool-palette-body");
  panel.appendChild(body);
  anchor.appendChild(panel);
  trigger.setAttribute("aria-controls", panel.id);

  /** 工具名以纯文本插入输入框：这里是聊天，不是表单——不生成 JSON 骨架，
   *  参数怎么写由人（和模型）用自然语言决定 */
  const insert = (rawName) => {
    const name = String(rawName || "");
    if (!name || input.disabled) return;
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? start;
    const before = input.value.slice(0, start);
    const after = input.value.slice(end);
    // 光标前是汉字/字母时补一个空格：否则会和上一个词黏成一个不存在的工具名
    const glue = before && !/\s$/.test(before) ? " " : "";
    const inserted = glue + name;
    input.value = before + inserted + after;
    const caret = before.length + inserted.length;
    input.setSelectionRange(caret, caret);
    input.dispatchEvent(new Event("input")); // 复用 chat.js 里既有的高度自适应
    setOpen(false);
    input.focus();
  };

  const render = (catalog) => {
    const plugins = Array.isArray(catalog && catalog.plugins) ? catalog.plugins : [];
    const tools = Array.isArray(catalog && catalog.tools) ? catalog.tools : [];
    const pluginMeta = new Map(plugins.map((p) => [String(p.id || ""), p]));

    // 按插件分组：插件的 tools 顺序由后端定，这里只按 tools 数组的到达顺序聚合，
    // 免得前端再排一次序把后端刻意的展示顺序打乱
    const groups = new Map();
    for (const tool of tools) {
      const id = String(tool.plugin || "");
      if (!groups.has(id)) groups.set(id, []);
      groups.get(id).push(tool);
    }

    const blocks = [];
    for (const [id, list] of groups) {
      const group = node("div", "palette-group");
      const title = node("div", "palette-group-title");
      title.appendChild(node("span", "palette-plugin", id || "未标注插件"));
      const meta = pluginMeta.get(id);
      if (meta && meta.title) title.appendChild(node("span", "palette-plugin-title", meta.title));
      if (meta && meta.version) title.appendChild(node("span", "palette-plugin-version", "v" + meta.version));
      group.appendChild(title);

      for (const tool of list) {
        const item = node("button", "palette-tool");
        item.type = "button";
        item.title = "点击插入工具名";
        const line = node("div", "palette-tool-line");
        line.appendChild(node("span", "palette-tool-name", tool.name));
        line.appendChild(riskBadge(tool.risk));
        item.appendChild(line);
        if (tool.description) item.appendChild(node("div", "palette-tool-desc", tool.description));
        const params = Object.keys(tool.args || {});
        if (params.length) {
          item.appendChild(node("div", "palette-tool-args", "参数：" + params.join("、")));
        }
        item.addEventListener("click", () => insert(tool.name));
        group.appendChild(item);
      }
      blocks.push(group);
    }

    if (!blocks.length) blocks.push(node("div", "tool-palette-msg", "后端没有装配出任何工具。"));

    const failures = Array.isArray(catalog && catalog.failures) ? catalog.failures : [];
    if (failures.length) {
      // 装配失败是「少了一个插件」的唯一线索，静默丢掉会让人对着缺失的工具名反复试
      const warn = node("div", "tool-palette-failures");
      warn.appendChild(node("div", "tool-palette-failures-title", `插件装配失败（${failures.length}）`));
      for (const failure of failures) {
        const plugin = String((failure && failure.plugin) || "未知插件");
        const error = String((failure && failure.error) || "无详情");
        warn.appendChild(node("div", "tool-palette-failure", `${plugin}：${error}`));
      }
      blocks.push(warn);
    }

    body.replaceChildren(...blocks);
  };

  const load = async () => {
    if (catalogCache) {
      render(catalogCache);
      return;
    }
    body.replaceChildren(node("div", "tool-palette-msg", "正在读取工具清单…"));
    try {
      catalogCache = await api.listTools();
      render(catalogCache);
    } catch (error) {
      body.replaceChildren(paletteError(`工具清单读取失败：${error.message}`, load));
    }
  };

  const setOpen = (open) => {
    panel.hidden = !open;
    trigger.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      void load();
      closeBtn.focus();
    }
  };

  trigger.addEventListener("click", () => setOpen(panel.hidden));
  closeBtn.addEventListener("click", () => setOpen(false));

  // 点面板外任意处关闭：面板是浮层，不关就会一直盖着消息区
  document.addEventListener("click", (event) => {
    if (panel.hidden || anchor.contains(event.target)) return;
    setOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || panel.hidden) return;
    setOpen(false);
    trigger.focus();
  });
}
