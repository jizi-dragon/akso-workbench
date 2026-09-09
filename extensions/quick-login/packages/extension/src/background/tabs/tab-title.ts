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