import type { ParallelAccount, ParallelAccountStatus, SiteGrant } from './types';

type Result<T> = { ok: true; data: T } | { ok: false; error: string };

/**
 * UI / content ⇄ background 的请求协议。
 *
 * 0.2.22 收敛：扩展只保留**执行面**所需的 kind——
 *   ① 账号数据面：`par.list`（读同步快照）/ `par.open`（开户并登录）；
 *   ② 轮盘：`wheel.toggle`；
 *   ③ 保留的休眠接口：`site.grants.*`（授权核心，0.2.21 用户定稿保留）、
 *      `par.grantChanged`（授权/停用名单变更后的规则重装钩子）、
 *      `ql.diag`（台架与现场诊断入口）。
 *
 * 已删除（唯一发送方是 0.2.13 退役的并行管理页，且与桌面数据面语义冲突——
 * `sync.ts` 每 2s 用桌面快照对账并删除快照外账号，本地增删改会在 2s 内被撤销）：
 *   `session.*`（旧会话模型）、`par.create/update/delete/moveBox/renameBox/deleteBox`、
 *   `par.probeScheme`（scheme 改由桌面快照随账号下发）、
 *   `data.export/data.import`（备份导出/恢复由桌面端负责）。
 */
export type RuntimeRequest =
  | { kind: 'site.grants.list' }
  | { kind: 'site.grant.add'; host: string }
  | { kind: 'par.list' }
  | { kind: 'par.open'; id: string; forceNewTab?: boolean }
  | { kind: 'par.grantChanged' }
  | { kind: 'ql.diag' }
  | { kind: 'wheel.toggle' };

export type RuntimeResponse =
  | { kind: 'site.grants.list'; result: Result<SiteGrant[]> }
  | { kind: 'site.grant.add'; result: Result<SiteGrant> }
  | { kind: 'par.list'; result: Result<Array<ParallelAccount & ParallelAccountStatus & { password: boolean }>> }
  | { kind: 'par.open'; result: Result<{ tabId: number; reused: boolean }> }
  | { kind: 'par.grantChanged'; result: Result<boolean> }
  | { kind: 'ql.diag'; result: Result<Record<string, unknown>> }
  | { kind: 'wheel.toggle'; result: Result<{ opened: boolean }> };

export type { Result };
