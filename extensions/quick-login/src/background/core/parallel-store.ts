import { db } from '../../storage/db';
import { SESSION_COLORS } from '../../shared/constants';
import { credentials } from './credentials';
import type { EncryptedCredentials, ParallelAccount } from '../../shared/types';

function newId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/** 并行账号持久化：页签名 / 账号名 / 加密密码 落 IndexedDB */
export const parallelStore = {
  list(): Promise<ParallelAccount[]> {
    return db.accounts.list();
  },

  async get(id: string): Promise<ParallelAccount> {
    const account = await db.accounts.get(id);
    if (!account) {
      throw new Error(`账号不存在: ${id}`);
    }
    return account;
  },

  async create(input: {
    siteHost: string;
    tabName: string;
    username: string;
    password: string;
    box?: string;
    scheme?: 'http' | 'https';
  }): Promise<ParallelAccount> {
    const now = Date.now();
    const existing = await db.accounts.list();
    const box = input.box?.trim();
    const account: ParallelAccount = {
      id: newId(),
      siteHost: input.siteHost,
      ...(input.scheme ? { scheme: input.scheme } : {}),
      tabName: input.tabName || input.username,
      username: input.username,
      color: SESSION_COLORS[existing.length % SESSION_COLORS.length],
      ...(box ? { box } : {}),
      createdAt: now,
      updatedAt: now,
      credentials: await credentials.encryptCredentials(input.username, input.password),
    };
    await db.accounts.put(account);
    return account;
  },

  /** 打开失败自学习（v3.10.9）：scheme 翻转写回账号档案 */
  async updateScheme(id: string, scheme: 'http' | 'https'): Promise<ParallelAccount> {
    const account = await this.get(id);
    const next: ParallelAccount = { ...account, scheme, updatedAt: Date.now() };
    await db.accounts.put(next);
    return next;
  },

  async updateTabName(id: string, tabName: string): Promise<ParallelAccount> {
    const account = await this.get(id);
    const next: ParallelAccount = { ...account, tabName, updatedAt: Date.now() };
    await db.accounts.put(next);
    return next;
  },

  /** 移入盒子（空串/空白 = 回到「默认盒子」，即移除 box 字段） */
  async updateBox(id: string, box: string): Promise<ParallelAccount> {
    const account = await this.get(id);
    const name = box.trim();
    const next: ParallelAccount = { ...account, updatedAt: Date.now() };
    if (name) {
      next.box = name;
    } else {
      delete next.box;
    }
    await db.accounts.put(next);
    return next;
  },

  /** 盒子重命名：盒内账号随迁；to 为空 = 并入「默认盒子」。返回随迁账号数 */
  async renameBox(from: string, to: string): Promise<number> {
    const fromName = from.trim();
    const toName = to.trim();
    if (!fromName) {
      throw new Error('源盒子名为空');
    }
    if (fromName === toName) {
      return 0;
    }
    const accounts = await db.accounts.list();
    let moved = 0;
    for (const account of accounts) {
      if ((account.box ?? '').trim() !== fromName) {
        continue;
      }
      const next: ParallelAccount = { ...account, updatedAt: Date.now() };
      if (toName) {
        next.box = toName;
      } else {
        delete next.box;
      }
      await db.accounts.put(next);
      moved++;
    }
    return moved;
  },

  /** 删除盒子：盒内账号全部回到「默认盒子」。返回随迁账号数 */
  async clearBox(name: string): Promise<number> {
    return this.renameBox(name, '');
  },

  async updateCredentials(id: string, creds: EncryptedCredentials): Promise<void> {
    const account = await this.get(id);
    await db.accounts.put({ ...account, credentials: creds, updatedAt: Date.now() });
  },

  async delete(id: string): Promise<void> {
    await db.accounts.delete(id);
  },
};
