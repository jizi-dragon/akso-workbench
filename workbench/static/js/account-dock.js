/** 右下角「当前账号」常驻卡片：**默认开放**，选中之后收成简化版。
 *
 *  ## 它解决什么
 *
 *  用户 2026-09-21 的两次要求：
 *
 *  > 「希望将选择变为选择卡片式的，成功选择卡片后，卡片 UI 则经过移动动画，
 *  >  变成简化版，显示在右下角……后续就默认使用选择的账号。」
 *  >
 *  > 「在右下角选择卡片的位置，默认开放。意思是用户进入新对话时，就可以立即选择账号。
 *  >  若用户没有第一时间选择账号，也可以通过与 agent 对话，来弹出选择页面。」
 *
 *  所以它有两个状态，**都常驻**（不再"没选就藏起来"）：
 *
 *  | 状态 | 长什么样 | 点它做什么 |
 *  |---|---|---|
 *  | 没选 | 「选择账号 · 这轮对话默认用它」 | 请服务端登记一次选择 → 弹卡片 |
 *  | 已选 | 账号 + 环境的小卡 | 展开完整信息 / 换一个 |
 *
 *  ## "后续就默认使用"分两半，两半必须都做到
 *
 *  | 谁需要知道 | 怎么知道 |
 *  |---|---|
 *  | **模型** | 模型自己问的那种：选择结果进工具观测（`approvals._answer`）。<br>**界面发起**的那种：服务端往会话里补一条 `note`（`approvals._append_conversation_note`）—— |
 *  | **人** | 就是这张卡片。它不该为了确认"现在用的是哪个账号"去翻对话 |
 *
 *  两半不一致是最糟的状态：模型按 A 账号干活，而界面上写着 B。
 *  所以卡片的内容**只来自服务端的答复**，前端不自己拼、也不缓存推断。
 *
 *  ## 为什么"换一个"不是前端开个下拉
 *
 *  候选账号由服务端按账号表展开，账号 id 要当参数用下去。前端自己拉一份清单
 *  就等于造出**第二个会漂的事实源**——而它错了不会报错，只会静静地选错账号。
 *  所以：
 *  - 默认入口 → 请服务端登记（`/api/agent/selection/pick`），由服务端给候选；
 *  - 「换一个账号」→ 把话说好放进输入框，由模型重新发起（它那边有一次确认在等）。
 *
 *  ## 安全与可达性
 *
 *  - 一切动态内容走 `textContent`（与 `chat-tools.js` / `approval-modal.js` 同源纪律）；
 *  - 尊重 `prefers-reduced-motion`：关掉动画仍要**立刻**出现在正确位置；
 *  - 卡片是 `<button>`，键盘可聚焦；面板展开时 `aria-expanded` 跟着走。
 */

import { api } from "./api.js";
import { state } from "./app.js";

/** 「选择账号」（默认入口）：`chat.js` 监听它，去服务端登记一次选择并弹卡片。
 *
 *  ⚠️ 这里**不能**直接 import `approval-modal.js` 的 `requestDecision`：那个模块
 *  反过来 import 本模块的 `dockSelection`（选中后要落到右下角），两边互相 import
 *  就是循环依赖。事件让依赖保持单向。
 *
 *  **「换一个账号」走的是同一个事件**（2026-09-21 用户反馈"点切换没反应"）：
 *  上一版它复用的是另一条路——"把一句话放进输入框，让模型重新问"。那是
 *  `/api/agent/selection/pick` 还不存在时的权宜之计；现在界面自己就能发起选择，
 *  再让用户按一次回车就纯属多余，而且他会以为按钮坏了。
 */
export const PICK_ACCOUNT_EVENT = "akso:pick-account";

let root = null;
let invite = null;
let inviteTitle = null;
let inviteSub = null;
let chip = null;
let chipLabel = null;
let chipEnv = null;
let chipAvatar = null;
let panel = null;
let panelMeta = null;
let panelHead = null;
let current = null;
let errorTimer = 0;

const INVITE_TITLE = "选择账号";
const INVITE_SUB = "这轮对话默认用它";

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined && text !== null) el.textContent = String(text);
  return el;
}

function reducedMotion() {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches === true;
  } catch {
    return false;
  }
}

function initials(label) {
  const text = String(label || "?").trim();
  return (text.slice(0, 2) || "?").toUpperCase();
}

function build() {
  if (root) return root;

  root = node("aside", "account-dock");
  root.setAttribute("aria-label", "当前使用的账号");

  // —— 默认态：还没选账号。**常驻可见**，否则用户根本不知道这里能选。
  invite = node("button", "account-invite");
  invite.type = "button";
  invite.title = "选一个账号，这轮对话默认用它";
  const inviteIcon = node("span", "account-invite-icon", "＋");
  const inviteText = node("span", "account-invite-text");
  inviteTitle = node("span", "account-invite-title", INVITE_TITLE);
  inviteSub = node("span", "account-invite-sub", INVITE_SUB);
  inviteText.appendChild(inviteTitle);
  inviteText.appendChild(inviteSub);
  invite.appendChild(inviteIcon);
  invite.appendChild(inviteText);
  invite.addEventListener("click", () => {
    document.dispatchEvent(new CustomEvent(PICK_ACCOUNT_EVENT));
  });
  root.appendChild(invite);

  // —— 已选态：简化卡片
  chip = node("button", "account-chip");
  chip.type = "button";
  chip.hidden = true;
  chip.setAttribute("aria-expanded", "false");
  chip.title = "点开看这个账号的完整信息";

  chipAvatar = node("span", "account-chip-avatar");
  const textWrap = node("span", "account-chip-text");
  chipLabel = node("span", "account-chip-label");
  chipEnv = node("span", "account-chip-env");
  textWrap.appendChild(chipLabel);
  textWrap.appendChild(chipEnv);
  chip.appendChild(chipAvatar);
  chip.appendChild(textWrap);
  chip.appendChild(node("span", "account-chip-caret", "›"));
  root.appendChild(chip);

  panel = node("div", "account-panel");
  panel.hidden = true;
  panelHead = node("div", "account-panel-head", "当前账号");
  panel.appendChild(panelHead);
  panelMeta = node("dl", "account-panel-meta");
  panel.appendChild(panelMeta);

  const actions = node("div", "account-panel-actions");
  const switchBtn = node("button", "btn-ghost account-switch", "换一个账号");
  switchBtn.type = "button";
  switchBtn.title = "重新打开账号选择（和你点「选择账号」是同一条路）";
  switchBtn.addEventListener("click", () => {
    setOpen(false);
    // 和默认入口**同一个事件**：界面直接请服务端登记一次新的选择。
    // 不走"让模型再问一遍"——那要多花一轮对话，而且用户会以为按钮没反应。
    document.dispatchEvent(new CustomEvent(PICK_ACCOUNT_EVENT));
  });
  actions.appendChild(switchBtn);
  panel.appendChild(actions);
  root.appendChild(panel);

  chip.addEventListener("click", () => setOpen(panel.hidden));

  document.body.appendChild(root);
  keepClearOfComposer();
  return root;
}

/** 让卡片**永远停在输入区上方**，不遮住输入框。
 *
 *  用户 2026-09-21 反馈："没有选择账号时，遮挡输入框"。原因是这里写死了
 *  `bottom: 22px`（视口底部），而输入区就在视口底部——两者必然重叠。
 *
 *  为什么用 JS 量而不是写死一个 `bottom`：输入区高度是**变的**（textarea 随内容
 *  长高、换行提示条出现/消失）。写死一个值只能挡住"我测试时的那个高度"。
 *  用 `ResizeObserver` 跟着它走，任何布局变化都自动让位。
 */
function keepClearOfComposer() {
  const composer = document.querySelector(".composer-wrap");
  if (!composer || typeof ResizeObserver !== "function") return;
  const sync = () => {
    const height = Math.round(composer.getBoundingClientRect().height);
    // 再留 14px 呼吸位，免得卡片贴着输入区边框
    document.documentElement.style.setProperty("--dock-bottom", `${height + 14}px`);
  };
  sync();
  const observer = new ResizeObserver(sync);
  observer.observe(composer);
  window.addEventListener("resize", sync);
}

function setOpen(open) {
  if (!panel) return;
  panel.hidden = !open;
  chip.setAttribute("aria-expanded", open ? "true" : "false");
  root.classList.toggle("is-open", open);
}

function paintMeta(selection) {
  panelMeta.replaceChildren();
  const rows = [["账号", selection.label]];
  if (selection.description) rows.push(["环境", selection.description]);
  for (const [key, value] of Object.entries(selection.meta || {})) {
    rows.push([key, value]);
  }
  for (const [key, value] of rows) {
    if (!value) continue;
    panelMeta.appendChild(node("dt", "account-panel-key", key));
    panelMeta.appendChild(node("dd", "account-panel-value", value));
  }
}

function paint(selection) {
  current = selection;
  chipLabel.textContent = String(selection.label || selection.value || "");
  chipEnv.textContent = String(selection.description || "");
  chipEnv.hidden = !selection.description;
  chipAvatar.textContent = initials(selection.label || selection.value);
  chip.title = `${selection.label || ""}${selection.description ? ` · ${selection.description}` : ""}`
    + " —— 点开看完整信息";
  paintMeta(selection);
}

/** 让卡片从**被点中的那张卡片**的位置飞过来并缩小。
 *
 *  用 FLIP 而不是克隆一个"飞行体"：克隆要在动画期间维持两棵近乎相同的 DOM，
 *  而这里唯一的观感目标是"它从那儿挪到了这儿"——把终点元素先摆好、再用
 *  反向 transform 播回去，既便宜又不会有两份内容的同步问题。
 */
function fly(fromRect) {
  if (!fromRect || reducedMotion()) return;
  const to = chip.getBoundingClientRect();
  if (!fromRect.width || !to.width) return;

  const dx = fromRect.left - to.left;
  const dy = fromRect.top - to.top;
  const scaleX = Math.max(0.12, fromRect.width / to.width);
  const scaleY = Math.max(0.12, fromRect.height / to.height);

  try {
    chip.animate(
      [
        { transform: `translate(${dx}px, ${dy}px) scale(${scaleX}, ${scaleY})`,
          opacity: 0.2, borderRadius: "14px" },
        { transform: "translate(0, 0) scale(1, 1)", opacity: 1, borderRadius: "16px" },
      ],
      { duration: 460, easing: "cubic-bezier(0.22, 1, 0.36, 1)" },
    );
    root.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 240, easing: "ease-out" });
  } catch {
    // Web Animations 不可用（老内核）时**什么都不做**：卡片已经在正确位置了，
    // 动画只是锦上添花，绝不能因为它挂了就让结果看不见。
  }
}

/** 落定一次选择：画出卡片，必要时从来源卡片飞过来。
 *
 *  `selection` 形如 `{value, label, description?, meta?}`——**来自服务端的答复**，
 *  不是前端拼的（见模块开头"两半必须一致"）。
 *
 *  ⚠️ `fromRect` 是**矩形快照**而不是元素：起点那张卡在弹窗里，而弹窗落定后就关了
 *  ——`display:none` 的元素量出来全是 0，动画会从屏幕左上角飞出来。
 *  所以调用方必须在关闭弹窗**之前**把矩形取下来。
 */
export function dockSelection(selection, { fromRect = null } = {}) {
  if (!selection || !(selection.value || selection.label)) return;
  build();
  setOpen(false);
  paint(selection);
  invite.hidden = true;
  chip.hidden = false;
  root.classList.remove("just-docked");
  void root.offsetWidth; // 重排一次，让下面的 class 与动画都从干净状态开始
  root.classList.add("just-docked");
  fly(fromRect);
}

/** 回到**默认态**（还没选账号）。
 *
 *  刻意不把整个浮层藏起来：用户 2026-09-21 明确要求"默认开放"——
 *  藏起来就等于"新对话里没有任何地方可以选账号"。
 */
export function clearSelection() {
  current = null;
  if (!root) return;
  setOpen(false);
  chip.hidden = true;
  invite.hidden = false;
}

/** 选择失败时就地报错（**不弹原生 alert，也不静默吞掉**）。
 *
 *  失败点集中在两处，而且都是用户看得懂、能自己解决的事：
 *  账号中心里一个账号都没有、或者会话建不出来。把它印在用户刚才点的那个位置上，
 *  比在别处冒一条 toast 更容易对上因果；几秒后自动收回默认文案。
 */
export function showDockError(message) {
  build();
  chip.hidden = true;
  invite.hidden = false;
  inviteTitle.textContent = "选择账号失败";
  inviteSub.textContent = String(message || "请稍后重试");
  root.classList.add("has-error");
  if (errorTimer) clearTimeout(errorTimer);
  errorTimer = window.setTimeout(() => {
    errorTimer = 0;
    root.classList.remove("has-error");
    inviteTitle.textContent = INVITE_TITLE;
    inviteSub.textContent = INVITE_SUB;
  }, 6000);
}

export function currentSelection() {
  return current;
}

/** 刷新页面后把卡片画回来。
 *
 *  选择活在服务端（进程内，按会话记），所以刷新不该让它消失
 *  ——但**不能动画**：页面刚加载完，把一张卡片从屏幕外"飞"进来毫无道理，
 *  那会让用户以为自己刚才点过什么。
 *
 *  失败一律静默落回默认态：它是一次补偿查询，不该影响对话本身
 *  （与 `recoverPending()` 同口径）。
 */
export async function restoreSelection(conversationId) {
  const id = String(conversationId || "");
  if (!id) {
    clearSelection();
    return;
  }
  try {
    const data = await api.getSelection(id);
    const selection = data && data.selection;
    if (selection && selection.value) dockSelection(selection);
    else clearSelection();
  } catch {
    clearSelection();
  }
}

/** 按**当前会话**重新拉一次选择。给"答复不是本页做出的"那条路用
 *  （例如在另一个标签页里点了卡片，本页只收到服务端的 `confirm_resolved`）。
 *
 *  这里读 `state.currentConversationId` 而不是让调用方传进来：会话 id 属于 app 状态，
 *  而调用方（弹窗）不该为了这一件事去认识它。
 */
export function refreshSelection() {
  return restoreSelection(state.currentConversationId);
}

export function initAccountDock() {
  build();
  // 先立即可见（默认开放），随后 `restoreSelection()` 按会话把它换成已选态
  invite.hidden = false;
  chip.hidden = true;
}
