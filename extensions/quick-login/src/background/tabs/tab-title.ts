import { CONTENT_MESSAGE } from '../../shared/constants';

/** 把标签页标题改写为账号名（权威写入；SPA 内导航由 title-hook 内容脚本持续维持） */
export async function setTabTitle(tabId: number, alias: string): Promise<void> {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      func: (title: string) => {
        if (document.title !== title) {
          document.title = title;
        }
      },
      args: [alias],
      world: 'MAIN',
    });
  } catch {
    // 页面不可注入（如 chrome:// 页面），忽略
  }
}

/**
 * 权威写入 + 通知内容脚本持续维持（SPA 内导航会重置标题）。
 *
 * 由 navigation（旧会话模型）与 parallel-session（并行模型）共用：此前两边各写一份
 * 完全相同的实现，改一处即两边行为漂移，现收敛到标题模块。
 */
export async function applyTitle(tabId: number, alias: string): Promise<void> {
  await setTabTitle(tabId, alias);
  try {
    await chrome.tabs.sendMessage(tabId, { type: CONTENT_MESSAGE.setTitle, alias });
  } catch {
    // 内容脚本未就绪：标题已权威写入，后续事件会再推
  }
}
