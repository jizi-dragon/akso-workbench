/**
 * Shield Bridge —— ISOLATED world，document_start 注入。
 * 在 MAIN 壳（shield-main.ts）与 background service worker 之间转发消息：
 * - 上行：window.postMessage({src:'QL_PAGE_TO_BRIDGE'}) → chrome.runtime.sendMessage
 * - 下行：chrome.runtime.onMessage(ql:bridgeDown) → window.postMessage
 */
import { CONTENT_MESSAGE, WINDOW_CHANNEL } from '../shared/constants';
import type { BridgeDownPayload, BridgeUpPayload } from '../shared/types';

function deliverDown(payload: BridgeDownPayload | null | undefined): void {
  if (!payload) {
    return;
  }
  window.postMessage(
    { src: WINDOW_CHANNEL.bridgeToPage, payload },
    '*',
  );
}

async function routeUp(payload: BridgeUpPayload): Promise<void> {
  try {
    const res = (await chrome.runtime.sendMessage({
      type: CONTENT_MESSAGE.bridgeUp,
      payload,
    })) as BridgeDownPayload | null | undefined;
    deliverDown(res ?? null);
  } catch {
    // SW 不可达时按未绑定处理，MAIN 壳保持直通模式
    deliverDown({ op: 'unbound' });
  }
}

window.addEventListener('message', (event: MessageEvent) => {
  if (event.source !== window) {
    return;
  }
  const data = event.data as { src?: string; payload?: unknown } | null;
  if (!data || data.src !== WINDOW_CHANNEL.pageToBridge || !data.payload) {
    return;
  }
  void routeUp(data.payload as BridgeUpPayload);
});

chrome.runtime.onMessage.addListener((msg: unknown) => {
  const m = msg as { type?: string; payload?: BridgeDownPayload } | null;
  if (m && m.type === CONTENT_MESSAGE.bridgeDown && m.payload) {
    deliverDown(m.payload);
  }
});
