import type { ParallelAccount, ParallelAccountStatus, Session, SiteGrant } from './types';

type Result<T> = { ok: true; data: T } | { ok: false; error: string };

/** UI / content ⇄ background 的请求协议 */
export type RuntimeRequest =
  | { kind: 'session.list' }
  | { kind: 'session.update'; id: string; patch: Partial<Pick<Session, 'name' | 'accountAlias' | 'color'>> }
  | { kind: 'session.delete'; id: string }
  | { kind: 'session.open'; id: string; host: string }
  | { kind: 'session.openOrCreate'; host: string; username?: string; password?: string; accountAlias?: string }
  | { kind: 'site.grants.list' }
  | { kind: 'site.grant.add'; host: string }
  | { kind: 'par.list' }
  | { kind: 'par.create'; siteHost: string; tabName: string; username: string; password: string; open: boolean; box?: string; scheme?: 'http' | 'https' }
  | { kind: 'par.probeScheme'; host: string }
  | { kind: 'par.update'; id: string; patch: Partial<Pick<ParallelAccount, 'tabName'>> }
  | { kind: 'par.moveBox'; id: string; box: string }
  | { kind: 'par.renameBox'; from: string; to: string }
  | { kind: 'par.deleteBox'; name: string }
  | { kind: 'par.delete'; id: string }
  | { kind: 'par.open'; id: string; forceNewTab?: boolean }
  | { kind: 'par.grantChanged' }
  | { kind: 'ql.diag' }
  | { kind: 'wheel.toggle' }
  | { kind: 'data.export' }
  | { kind: 'data.import'; data: DataBackup };

/** 备份文件结构（v1）：种子 + 加密凭证 + 授权站 + 盒子配置（见 tmp 导出脚本） */
export interface DataBackup {
  format: 'quicklogin-backup';
  version: 1;
  exportedAt: string;
  /** 源设备加密种子（导入端用它解密凭证，再以本地种子重加密入库） */
  cryptoSeed: string;
  /** 授权站点 host 清单 */
  sites: string[];
  boxes: { default?: string; remembered?: string[]; disabled?: string[] };
  accounts: Array<{
    siteHost: string;
    tabName: string;
    box?: string;
    credentials: {
      encryptedUsername: string;
      encryptedPassword: string;
      iv: string;
      ivPassword: string;
      encryptedAt?: number;
    } | null;
  }>;
}

export type RuntimeResponse =
  | { kind: 'session.list'; result: Result<Session[]> }
  | { kind: 'session.update'; result: Result<Session> }
  | { kind: 'session.delete'; result: Result<void> }
  | { kind: 'session.open'; result: Result<{ tabId: number }> }
  | { kind: 'session.openOrCreate'; result: Result<{ tabId: number; sessionId: string; reused: boolean }> }
  | { kind: 'site.grants.list'; result: Result<SiteGrant[]> }
  | { kind: 'site.grant.add'; result: Result<SiteGrant> }
  | { kind: 'par.list'; result: Result<Array<ParallelAccount & ParallelAccountStatus & { password: boolean }>> }
  | { kind: 'par.create'; result: Result<ParallelAccount> }
  | { kind: 'par.probeScheme'; result: Result<'http' | 'https'> }
  | { kind: 'par.update'; result: Result<ParallelAccount> }
  | { kind: 'par.moveBox'; result: Result<ParallelAccount> }
  | { kind: 'par.renameBox'; result: Result<{ moved: number }> }
  | { kind: 'par.deleteBox'; result: Result<{ moved: number }> }
  | { kind: 'par.delete'; result: Result<void> }
  | { kind: 'par.open'; result: Result<{ tabId: number; reused: boolean }> }
  | { kind: 'par.grantChanged'; result: Result<boolean> }
  | { kind: 'ql.diag'; result: Result<Record<string, unknown>> }
  | { kind: 'wheel.toggle'; result: Result<{ opened: boolean }> }
  | { kind: 'data.export'; result: Result<DataBackup> }
  | { kind: 'data.import'; result: Result<{ created: number; skipped: number; hosts: string[] }> };

export type { Result };
