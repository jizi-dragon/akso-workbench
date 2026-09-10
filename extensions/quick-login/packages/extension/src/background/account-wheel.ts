/**
 * 账号轮盘开关（三级降级）：页面内浮层 → 独立小窗 → 标签页。
 *
 * 独立成模块的原因：桌面指令通道（sync.ts）需要直调本函数——
 * 曾用 chrome.runtime.sendMessage 从 SW 发给自身，Chrome 语义不投递给
 * 发送者上下文，wheel.toggle 指令静默丢失（0.2.4 实锤断点）。
 */

const WHEEL_PAGE = 'ui/wheel/wheel.html';
const WHEEL_W = 720;
const WHEEL_H = 760;

const WHEEL_OVERLAY_FILE = 'content/wheel-overlay.js';

let wheelWinId: number | null = null;
/** 触发去抖：命令重放/系统连击不会开后又立刻关 */
let lastToggleAt = 0;

chrome.windows.onRemoved.addListener((winId) => {
  if (winId === wheelWinId) {
    wheelWinId = null;
  }
});

export async function toggleAccountWheel(): Promise<void> {
  const now = Date.now();
  if (now - lastToggleAt < 300) {
    return;
  }
  lastToggleAt = now;

  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });

  // 机制一（主）：普通网页 → 页面内无框浮层（再次触发 = 脚本自关闭）
  try {
    if (tab?.id && tab.url && /^https?:/i.test(tab.url)) {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: [WHEEL_OVERLAY_FILE],
        world: 'ISOLATED',
      });
      return;
    }
  } catch {
    // 注入失败（受限页/权限收回等）→ 继续降级
  }

  // 机制二：独立弹窗小窗（Chrome 对 chrome:// 等页注入不了时仍可用）
  if (wheelWinId !== null) {
    try {
      await chrome.windows.get(wheelWinId);
    } catch {
      wheelWinId = null;
    }
    if (wheelWinId !== null) {
      await chrome.windows.remove(wheelWinId).catch(() => undefined);
      wheelWinId = null;
      return;
    }
  }

  const current = tab ? await chrome.windows.get(tab.windowId).catch(() => undefined) : undefined;
  const left =
    current && typeof current.left === 'number'
      ? Math.max(0, current.left + Math.max(0, ((current.width ?? 900) - WHEEL_W) >> 1))
      : undefined;
  const top =
    current && typeof current.top === 'number'
      ? Math.max(0, current.top + Math.max(0, ((current.height ?? 700) - WHEEL_H) >> 1))
      : undefined;

  try {
    const win = await chrome.windows.create({
      url: chrome.runtime.getURL(WHEEL_PAGE),
      type: 'popup',
      width: WHEEL_W,
      height: WHEEL_H,
      left,
      top,
    });
    wheelWinId = win.id ?? null;
    return;
  } catch {
    // 继续走最终兜底
  }

  // 机制三（最终）：普通标签页打开轮盘
  await chrome.tabs.create({ url: chrome.runtime.getURL(WHEEL_PAGE) });
}
