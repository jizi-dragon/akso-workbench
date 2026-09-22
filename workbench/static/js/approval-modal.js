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
 *   ## 第四种形态：**让用户挑一个**（2026-09-21）
 *
 *   同一条实时确认通道还承载**选择**：`request.options` 非空时，弹窗不画「同意 / 拒绝」，
 *   而是画一栅格候选卡片（`options_source="accounts"` 时就是账号中心里的账号）。
 *   点中一张 = 答复，答复的 `value` 就是那个账号的 id。
 *
 *   为什么复用它而不是新开一条通道：选择与确认的**时序完全一样**——后端有一个对话循环
 *   正卡在等待里、有一个 pendingId、有一次超时、有一条"稍后"待办。差别只在
 *   "用户能答什么"，而那正好就是载荷里多出来的一个 `options` 字段。
 *
 *   选中之后卡片会**飞**到右下角收成简化版（`account-dock.js`），用户从此一眼看得到
 *   "这轮对话在用哪个账号"，不必去翻对话——而模型那边由 `approvals._answer()` 告知同一件事。
 *
 *   ## 与风险门那张卡的分工（别读混）
 *
 *   - `confirmation`（模型主动问）：弹窗出现时**循环正卡在等待里**，点完模型自己继续。
 *   - `approval`（宿主风险门）：**2026-09-22 起也一样**——循环就地等答复，
 *     放行后由**服务端用完全相同的参数执行**那一次调用，再把真实结果喂给模型
 *     （决策卡 G2）。在此之前它只发事件不等，于是"用户放行时没有任何人在等"，
 *     批准被搁置、界面表现为"我点了放行，什么都没发生"。
 *     超时语义两边一致：到点按**未同意**处理，绝不默认同意。
 *
 *   两者的标题与说明文案因此不同，`kind` 字段就是为这件事存在的。
 *
 *   ## 安全基线
 *
 *   与 `chat-tools.js` 同源：一切动态内容走 `textContent`，**绝不 innerHTML 拼接**；
 *   令牌是一次性凭证，**不回填 DOM、不落日志**。
 */

import { api } from "./api.js";
import { dockSelection, refreshSelection } from "./account-dock.js";

/** 单条待办条的最小信息量：够标题一行 + 重新打开 */
const pending = new Map(); // pendingId → request
let queue = []; // 等待展示的请求
let current = null; // 当前展示的
let dialog = null;
let strip = null;
let pendingDock = null;
/** 已经选好、等弹窗关掉之后再落到右下角的那一次选择（见 `advance()`）。 */

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

/* ———————————————— 说明文字 → 标题 + 要点 ———————————————— */

/** 要点先露几条：超出的折起来（渐进披露）。
 *
 *  用户 2026-09-22 原话：「弹窗不要放一堆文字上去……用户在执行时没有时间去仔细看
 *  UI 上比蚂蚁还小的字」。4 条是"一屏扫完还能记住"的量级；再多就不是读，是翻。 */
const MAX_POINTS = 4;

/** 一行的最大长度：超过它就不再当"一句话"，而是按句读切开。
 *  纯文本长行在弹窗里换行后会变成一坨，切句之后每行一件事。 */
const POINT_MAX_CHARS = 88;

function textLines(raw) {
  return String(raw ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** 按中英文句读切句（不用 lookbehind：老引擎上会直接语法报错）。 */
function splitSentences(text) {
  const out = [];
  let buffer = "";
  for (const ch of text) {
    buffer += ch;
    if ("。；;！!？?".includes(ch)) {
      if (buffer.trim()) out.push(buffer.trim());
      buffer = "";
    }
  }
  if (buffer.trim()) out.push(buffer.trim());
  return out;
}

/** 标题 = 第一行（弹窗按 16px 加粗渲染，所以它必须自成一句）。 */
function headlineOf(primary) {
  return textLines(primary)[0] || "（没有说明）";
}

/** 要点 = `primary` 的其余行 + `details`，双向都支持 `·`/`-`/`*` 前缀。
 *
 *  为什么还要切句：`details` 常常是模型写的一整段散文（换行都没有），
 *  不切的话它就会以"一大坨"的形态出现——那正是用户抱怨的东西。 */
function briefingPoints(primary, details) {
  const lines = [...textLines(primary).slice(1), ...textLines(details)];
  const points = [];
  for (const line of lines) {
    const marked = /^[·•*\-–—]\s*/.test(line);
    const text = line.replace(/^[·•*\-–—]\s*/, "");
    if (!text) continue;
    if (marked || text.length <= POINT_MAX_CHARS) points.push(text);
    else points.push(...splitSentences(text));
  }
  return points;
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
  // 要点列表（`·` / `-` 开头的行，以及被切开的长段落）。
  // 2026-09-22 用户下达：「弹出的蓝图/审核界面，请你不要放一堆文字上去，请设计排版，
  // 言简意赅，用户在执行时没有时间去仔细看 UI 上比蚂蚁还小的字」
  // ⇒ 说明文字一律**先给标题、再给要点**，超过 MAX_POINTS 条折起来（渐进披露）。
  const points = node("ul", "tk-decision-points");
  card.appendChild(points);
  const more = node("button", "tk-more");
  more.type = "button";
  more.hidden = true;
  card.appendChild(more);
  const details = node("div", "tk-decision-details");
  card.appendChild(details);

  // **选择型确认**（`request.options` 非空）把候选画在这里，而不是问「同意 / 拒绝」。
  // 它不是"要不要做"，而是"用哪一个"——给一组按钮问"同意吗"是答非所问。
  const choices = node("div", "tk-choice-grid");
  choices.hidden = true;
  card.appendChild(choices);

  // 技术参数（`blueprint_id` / `account_id` 这类 UUID）：**默认收起**。
  // 旧实现把它摊在脸上（等宽 11px + 220px 滚动区），在"几秒钟做判断"的场景里
  // 它就是纯噪声——但它又是唯一可核对的事实，所以留着，只是不再挡路。
  const argsToggle = node("button", "tk-disclosure");
  argsToggle.type = "button";
  argsToggle.hidden = true;
  argsToggle.setAttribute("aria-expanded", "false");
  card.appendChild(argsToggle);

  const targetBlock = node("div", "tk-decision-target");
  targetBlock.hidden = true;
  const argsEl = node("pre", "tool-args");
  targetBlock.appendChild(argsEl);
  card.appendChild(targetBlock);

  argsToggle.addEventListener("click", () => {
    const open = targetBlock.hidden;
    targetBlock.hidden = !open;
    argsToggle.setAttribute("aria-expanded", open ? "true" : "false");
    argsToggle.textContent = (open ? "▾ " : "▸ ") + (argsToggle.dataset.label || "技术参数");
  });

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

  const ui = { box, title, badges, question, points, more, details, choices,
               argsToggle, targetBlock, argsEl, meta, actions, state,
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
  // 方向键在候选卡片之间走。卡片是栅格，但**列数由 CSS 决定**（窄屏一列、宽屏两列），
  // 所以 JS 里算不出"上一行"，只做线性首尾相接——两种排布下都不会卡住。
  dialog.choices.addEventListener("keydown", (event) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (step === undefined) return;
    event.preventDefault();
    moveChoiceFocus(step);
  });
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

/** 一张候选卡片。整张卡就是一个 `<button>`——点它 = 选中它。
 *
 *  结构（三栏一行的栅格，见 `chat-tools.css` 的 `.tk-choice-card`）：
 *
 *      [头像] 账号名                      ✓
 *             环境名
 *      [地址] [分组] [备注]  ← 只在有值时出现
 *
 *  内容全部走 `textContent`（本模块的安全基线）：候选项里可能有用户自己填的
 *  账号备注、环境名，那些是**用户数据**，不是模板。
 *
 *  2026-09-21 重做（用户："弹出 UI 太丑了"）：上一版是"一列文字的堆叠 + 一个绝对
 *  定位的『选它』"，中文键值还用了等宽字体。现在按**账号卡片**来排版——头像给
 *  一眼可辨的形状，环境名紧跟在账号名下面（这两样是人真正在选的东西），
 *  地址/分组/备注降级成底部的小标签。
 */
function choiceCard(option) {
  const value = String(option.value ?? "");
  const label = String(option.label || value);
  const card = node("button", "tk-choice-card");
  card.type = "button";
  card.dataset.value = value;

  const avatar = node("span", "tk-choice-avatar");
  avatar.textContent = (label.trim().slice(0, 2) || "?").toUpperCase();

  const main = node("span", "tk-choice-main");
  main.appendChild(node("span", "tk-choice-label", label));
  if (option.description) {
    main.appendChild(node("span", "tk-choice-desc", option.description));
  }

  const mark = node("span", "tk-choice-mark", "✓");

  card.appendChild(avatar);
  card.appendChild(main);
  card.appendChild(mark);

  const meta = option.meta && typeof option.meta === "object" ? Object.entries(option.meta) : [];
  if (meta.length) {
    const wrap = node("span", "tk-choice-meta");
    for (const [key, raw] of meta) {
      const tag = node("span", "tk-choice-tag");
      tag.appendChild(node("span", "tk-choice-tag-key", key));
      tag.appendChild(node("span", "tk-choice-tag-value", raw));
      wrap.appendChild(tag);
    }
    card.appendChild(wrap);
  }
  card.addEventListener("click", () => void chooseOption(card));
  return card;
}

/** 方向键在卡片之间移动焦点。
 *
 *  卡片是栅格排布，但**列数由 CSS 决定**（窄屏一列、宽屏两列），所以在 JS 里算
 *  "上一行 / 下一行"必然算错。用线性首尾相接：左右上下都是"前一个 / 后一个"，
 *  在两种排布下都不会卡住。
 */
function moveChoiceFocus(step) {
  if (!dialog) return;
  const cards = [...dialog.choices.querySelectorAll(".tk-choice-card")];
  if (!cards.length) return;
  const at = cards.indexOf(document.activeElement);
  const next = at < 0 ? 0 : (at + step + cards.length) % cards.length;
  cards[next].focus();
}

function chooseOption(card) {
  if (!current) return;
  // 矩形快照必须在这里取：`decide()` 提交成功后弹窗会被关掉，
  // 那时这张卡已经 `display:none`，量出来是 0（见 account-dock.js 的 fly）
  const rect = card.getBoundingClientRect();
  void decide(true, card.dataset.value || "", rect);
}

function show(request) {
  const ui = ensureDialog();
  current = request;

  const isConfirmation = request.kind !== "approval";
  const options = isConfirmation && Array.isArray(request.options) ? request.options : [];
  const isChoice = options.length > 0;
  const isLocalChoice = isChoice && request.local === true;

  ui.title.textContent = isChoice
    ? "选择账号"
    : (isConfirmation ? "模型请求确认" : "需要你放行");
  ui.state.hidden = true;
  ui.state.textContent = "";
  ui.actions.hidden = false;
  ui.allow.disabled = false;
  ui.deny.disabled = false;
  ui.later.disabled = false;
  // 选择题没有"同意"这个动作：候选本身就是答案，「同意」等于没回答。
  ui.allow.hidden = isChoice;
  // 「都不合适」= 拒绝（可填理由），它的价值在于**把理由回给模型**——否则模型只会
  // 一遍遍重问同样的几个候选。但**用户自己点开**的那种选择后面没有任何东西在等，
  // 拒绝也就没有接收方，摆着只会让人以为按了会发生什么。
  ui.deny.hidden = isLocalChoice;
  ui.deny.textContent = isChoice ? "都不合适" : "拒绝";
  // 同理，「稍后」对一次自己点开的选择就只是"关掉"——它不会变成待办条。
  ui.later.textContent = isLocalChoice ? "关闭" : "稍后";
  ui.later.title = isLocalChoice
    ? "关掉这个窗口。右下角那张卡还在，随时可以再打开"
    : "先收起来（不算答复）——它不会消失，会停在输入框上方";
  // 选择题要摆一栅格卡片，460px 的默认宽度放不下（卡片会挤成一列长条）
  ui.box.classList.toggle("is-choice", isChoice);

  ui.badges.replaceChildren();
  if (request.risk) ui.badges.appendChild(riskBadge(request.risk));
  if (request.tool) ui.badges.appendChild(node("span", "tk-decision-tool mono", request.tool));

  const primary = isChoice
    ? (request.question || "要用哪个账号继续？")
    : (isConfirmation
      ? (request.question || "（模型没有说明要确认什么）")
      : (request.summary || "（平台未给出这次调用的说明）"));
  ui.question.textContent = headlineOf(primary);

  // 要点：其余的行 + `details`，逐条渲染；超过 MAX_POINTS 条先折起来。
  ui.points.replaceChildren();
  const points = briefingPoints(primary, isChoice ? "" : request.details);
  ui.points.hidden = points.length === 0;
  points.forEach((text, index) => {
    const li = node("li", "tk-point", text);
    if (index >= MAX_POINTS) li.hidden = true;
    ui.points.appendChild(li);
  });
  ui.more.hidden = points.length <= MAX_POINTS;
  ui.more.textContent = `展开其余 ${Math.max(0, points.length - MAX_POINTS)} 条`;
  ui.more.setAttribute("aria-expanded", "false");
  ui.more.onclick = () => {
    const expanded = ui.more.getAttribute("aria-expanded") === "true";
    [...ui.points.children].forEach((li, index) => {
      if (index >= MAX_POINTS) li.hidden = expanded;
    });
    ui.more.setAttribute("aria-expanded", expanded ? "false" : "true");
    ui.more.textContent = expanded
      ? `展开其余 ${points.length - MAX_POINTS} 条`
      : "收起";
  };

  ui.choices.replaceChildren();
  ui.choices.hidden = !isChoice;
  if (isChoice) {
    for (const option of options) ui.choices.appendChild(choiceCard(option));
  }

  // 一句话说清"点了会发生什么"。风险门与实时确认的**区别在谁接手**，所以文案必须不同
  // （抄错方向会让人对着一个已经复活的循环干等）：
  //   · 风险门：循环就地等 → 放行后**服务端**用相同参数执行（2026-09-22 决策卡 G2）
  //   · 实时确认：循环就地等 → 点完**模型**自己接着往下走
  ui.details.replaceChildren();
  if (isConfirmation && request.details) {
    ui.details.hidden = true; // details 已经进了上面的要点列表，这里不再重复一遍
  } else if (!isConfirmation) {
    ui.details.textContent = "放行后立即用相同参数执行，结果回到这一轮对话。超时按未同意处理。";
    ui.details.hidden = false;
  } else {
    ui.details.hidden = true;
  }

  const target = isConfirmation ? request.target : null;
  const argSource = (target && target.args && Object.keys(target.args).length)
    ? target.args
    : (request.args || {});
  const argKeys = Object.keys(argSource);
  if (argKeys.length) {
    ui.argsEl.textContent = prettyArgs(JSON.stringify(argSource));
    ui.argsToggle.dataset.label = `技术参数（${argKeys.join("、")}）`;
    ui.argsToggle.textContent = "▸ " + ui.argsToggle.dataset.label;
    ui.argsToggle.setAttribute("aria-expanded", "false");
    ui.argsToggle.hidden = false;
    ui.targetBlock.hidden = true;
  } else {
    ui.argsToggle.hidden = true;
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
  // 选择落定：**弹窗关掉之后**才画右下角那张卡，动画的起点才对得上
  // （起点矩形在 `decide()` 里、弹窗关闭之前就取好了——关掉之后原卡片量不到几何）
  const dock = pendingDock;
  pendingDock = null;
  if (dock) dockSelection(dock.selection, { fromRect: dock.fromRect });
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

async function decide(approved, value = "", fromRect = null) {
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
    if (approved) await api.approvePending(request.pendingId, value);
    else await api.rejectPending(request.pendingId, reason);
    // 令牌是一次性凭证，**不回填 DOM**。确认门那条路循环本来就不需要它：
    // 服务端按「工具 + 参数指纹」认领这次批准。
    ui.state.className = `tk-decision-state is-${approved ? "allowed" : "rejected"}`;
    const chosen = value
      ? (request.options || []).find((option) => String(option.value) === String(value))
      : null;
    ui.state.textContent = approved
      ? (chosen
        ? `已选定「${chosen.label}」· 模型按它继续`
        : (request.kind === "approval"
          ? "已放行 · 正在用相同参数执行（结果会回到这轮对话）"
          : "已同意 · 模型继续"))
      : "已拒绝 · 模型不会执行";
    // 选择的结果要在**弹窗关掉之后**才落到右下角：弹窗还盖着屏幕时飞过去，
    // 用户只会看到一张卡在遮罩底下动了一下。真正的落点在 `advance()`。
    if (approved && chosen) {
      pendingDock = {
        selection: {
          value: String(chosen.value ?? value),
          label: chosen.label || String(value),
          description: chosen.description || "",
          meta: chosen.meta || {},
        },
        fromRect,
      };
    }
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
 *  `{pendingId, kind, local?, tool, risk, question?, details?, timeoutS?, target?,
 *    summary?, args?, options?}`
 *
 *  `local: true` = **用户自己点开的选择**（右下角那张卡），不是 agent 卡在等待里问的。 */
export function requestDecision(request) {
  if (!request || !request.pendingId) return;
  if (pending.has(request.pendingId)) return; // 重放（SSE 重连）不重复弹
  // **用户主动发起的选择不进待办条**。`pending` 的语义是"有人在等你答复"——它是
  // 「稍后」的落点，会在输入框上方留一条横幅。而一次自己点开的选择背后**没有任何
  // 东西在等**：塞进去之后，用户点「关闭」反而多出一条"等你拍板"的横幅，而他只是
  // 把窗口关了（2026-09-21 用户反馈第 4 条："这是用户主动选择，不是 agent 发起"）。
  if (!request.local) pending.set(request.pendingId, request);
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
      const wasChoice = Array.isArray(current.options) && current.options.length > 0;
      ui.state.textContent = wasChoice
        ? "已选定 · 模型按它继续"
        : (current.kind === "approval"
          ? "已放行 · 现在让模型重试同一次调用即可执行"
          : "已同意 · 模型继续");
      if (wasChoice) {
        // 答复**不是本页做出的**（比如在另一个标签页点了卡片）：本页手里没有那个 value，
        // 但服务端有权威记录。拉回来把右下角那张卡补上——否则会出现"模型按 A 干活、
        // 本页右下角还写着 B（或者什么都没有）"，而两边不一致比不显示更糟。
        void refreshSelection();
      }
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
