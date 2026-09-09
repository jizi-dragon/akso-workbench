import { db } from '../../storage/db';
import { SESSION_COLORS } from '../../shared/constants';
import type { EncryptedCredentials, Session } from '../../shared/types';

function newId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export const sessionManager = {
  list(): Promise<Session[]> {
    return db.sessions.list();
  },

  async get(id: string): Promise<Session | undefined> {
    return db.sessions.get(id);
  },

  async getOrThrow(id: string): Promise<Session> {
    const session = await db.sessions.get(id);
    if (!session) {
      throw new Error(`会话不存在: ${id}`);
    }
    return session;
  },

  async create(input: { name: string; accountAlias: string; siteHost: string }): Promise<Session> {
    const now = Date.now();
    const existing = await db.sessions.list();
    const session: Session = {
      id: newId(),
      name: input.name || input.accountAlias || input.siteHost,
      accountAlias: input.accountAlias,
      siteHost: input.siteHost,
      color: SESSION_COLORS[existing.length % SESSION_COLORS.length],
      createdAt: now,
      updatedAt: now,
    };
    await db.sessions.put(session);
    return session;
  },

  async update(id: string, patch: Partial<Pick<Session, 'name' | 'accountAlias' | 'color'>>): Promise<Session> {
    const session = await db.sessions.get(id);
    if (!session) {
      throw new Error(`会话不存在: ${id}`);
    }
    const next = { ...session, ...patch, updatedAt: Date.now() };
    await db.sessions.put(next);
    return next;
  },

  /** 打开失败自学习（v3.10.9）：session.scheme 翻转写回 */
  async updateScheme(id: string, scheme: 'http' | 'https'): Promise<void> {
    const session = await db.sessions.get(id);
    if (!session) {
      return;
    }
    await db.sessions.put({ ...session, scheme, updatedAt: Date.now() });
  },

  /** 保存/更新加密后的账号密码 */
  async updateCredentials(id: string, credentials: EncryptedCredentials): Promise<Session> {
    const session = await db.sessions.get(id);
    if (!session) {
      throw new Error(`会话不存在: ${id}`);
    }
    const next = { ...session, credentials, updatedAt: Date.now() };
    await db.sessions.put(next);
    return next;
  },

  /** 删除会话记录；已打开的标签页不受影响（绑定按标签关闭清理） */
  async delete(id: string): Promise<void> {
    await db.sessions.delete(id);
  },
};
