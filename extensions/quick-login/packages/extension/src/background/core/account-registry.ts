import { sessionManager } from './session-manager';

/**
 * 缓存会话 alias，供标签标题改写与内容脚本使用。
 * 删除/改名后调用 invalidate 使之最终一致。
 */
const cache = new Map<string, string>();

export const accountRegistry = {
  async getAlias(sessionId: string): Promise<string | undefined> {
    const hit = cache.get(sessionId);
    if (hit !== undefined) {
      return hit;
    }
    const session = await sessionManager.get(sessionId);
    const alias = session?.accountAlias || session?.name;
    if (session) {
      cache.set(sessionId, alias ?? '');
    }
    return alias;
  },

  invalidate(sessionId?: string): void {
    if (sessionId) {
      cache.delete(sessionId);
    } else {
      cache.clear();
    }
  },
};