/**
 * 凭据来源抽象 —— 「账号条」只依赖这份接口，不关心凭据存在哪里。
 *
 * 现在唯一的实现是 akso-cloud（`cloudCredentialProvider`）：账号与口令都存在云端，
 * 口令用 Fernet 加密落库。**本进程从不接触明文口令** —— 它只拿账号 id 去换一张已经签好的
 * eGMP JWT（`POST /api/accounts/:id/egmp-token`，云端解密口令、自己调 Auth，只回票）。
 *
 * 2026-11-05 改动：原来这里是 `GET /api/accounts/:id/password` 现取口令、本机换票；
 * 用户要求"把暴露密码的端点关掉"，于是改成云端代登录，口令一步都不出云端。
 *
 * 以后要换来源（本地钥匙串 / 企业 SSO / 临时口令），只要再写一个实现即可，
 * 页面与登录流程都不用动。
 */
import type { CloudAccount } from './cloud.ts'
import { egmpToken } from './cloud.ts'

/** 账号条要展示的一条凭据（不含口令，`hasPassword` 只是"有没有存"）。 */
export interface CredentialAccount {
  id: string
  /** 展示名：页签名，没有就用平台账号。 */
  label: string
  /** 平台账号（eGMP 的 account）。 */
  account: string
  /** eGMP 站点地址。 */
  baseUrl: string
  siteName: string
  hasPassword: boolean
}

/** 云端代登录换来的一张票 + 它属于哪个站点/账号。 */
export interface MintedToken {
  baseUrl: string
  account: string
  token: string
  /** JWT 的 `exp`（毫秒）；云端解不出来时为 undefined。 */
  expiresAt?: number
}

export interface CredentialProvider {
  /** 能拿来登录 eGMP 的账号（来自账号库快照）。 */
  list(): CredentialAccount[]
  /** 按 id 让云端代登录，拿到一张 eGMP JWT。只在 host 内调用。 */
  mint(accountId: string): Promise<MintedToken>
  /** 账号条当前连接的那个账号。 */
  currentAccountId(): string | null
}

export function toCredentialAccount(account: CloudAccount): CredentialAccount {
  return {
    id: account.id,
    label: account.tabName.trim() !== '' ? account.tabName : account.username,
    account: account.username,
    baseUrl: account.baseUrl,
    siteName: account.siteName,
    hasPassword: account.hasPassword,
  }
}

/**
 * akso-cloud 实现。依赖由调用方注入（快照与云端会话都活在 host 的闭包里），
 * 这样这个模块不碰任何全局状态，测试里可以塞假数据。
 */
export function cloudCredentialProvider(deps: {
  accounts: () => CloudAccount[]
  cloudSession: () => { baseUrl: string; token: string } | null
  currentId: () => string | null
}): CredentialProvider {
  return {
    list: () => deps.accounts().map(toCredentialAccount),
    currentAccountId: () => deps.currentId(),
    async mint(accountId: string): Promise<MintedToken> {
      const account = deps.accounts().find((item) => item.id === accountId)
      if (account === undefined) throw new Error(`账号库里没有这个账号：${accountId}`)
      if (!account.hasPassword) throw new Error('这个账号在 akso-cloud 里没有存口令，无法用它换票')
      const session = deps.cloudSession()
      if (session === null) throw new Error('akso-cloud 未登录，换不到 eGMP 令牌')
      const minted = await egmpToken(session.baseUrl, session.token, accountId)
      return {
        baseUrl: minted.baseUrl !== '' ? minted.baseUrl : account.baseUrl,
        account: minted.account !== '' ? minted.account : account.username,
        token: minted.token,
        expiresAt: minted.expiresAt,
      }
    },
  }
}
