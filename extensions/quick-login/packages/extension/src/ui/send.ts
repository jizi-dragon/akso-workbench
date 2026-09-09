import type { Result, RuntimeRequest, RuntimeResponse } from '../shared/messages';

export function send(req: RuntimeRequest): Promise<RuntimeResponse> {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(req, resolve);
  });
}

export function okOf<T>(result: Result<T>, fallback: T): T {
  return result.ok ? result.data : fallback;
}