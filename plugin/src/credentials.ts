/**
 * 凭据来源抽象 —— 「账号条」只依赖这份接口，不关心口令存在哪里。
 *
 * 现在唯一的实现是 akso-cloud（`cloudCredentialProvider`）：账号与口令都存在云端，
 * 口令用 Fernet 加密落库，本机按账号 id 现取现用。**明文只在 host 进程里出现一次**：
 * 直接喂给 `POST /api/openapi/v1.0/Auth` 换 JWT，然后就被丢掉 ——
 * 既不回给浏览器，也不进工具参数、不进日志、不落盘。
 *
 * 以后要换来源（本地钥匙串 / 企业 SSO / 临时口令），只要再写一个实现即可，
 * 页面与登录流程都不用动。
 */
import type { CloudAccount } from './cloud.ts'
import { revealPassword } from './cloud.ts'

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

/** 现取的口令 + 该发给哪个站点、哪个账号。 */
export interface RevealedCredential {
  baseUrl: string
  account: string
  password: string
}

export interface CredentialProvider {
  /** 能拿来登录 eGMP 的账号（来自账号库快照）。 */
  list(): CredentialAccount[]
  /** 按 id 取明文口令。只在 host 内调用。 */
  reveal(accountId: string): Promise<RevealedCredential>
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
    async reveal(accountId: string): Promise<RevealedCredential> {
      const account = deps.accounts().find((item) => item.id === accountId)
      if (account === undefined) throw new Error(`账号库里没有这个账号：${accountId}`)
      if (!account.hasPassword) throw new Error('这个账号在 akso-cloud 里没有存口令，无法用它登录')
      const session = deps.cloudSession()
      if (session === null) throw new Error('akso-cloud 未登录，取不到账号口令')
      const password = await revealPassword(session.baseUrl, session.token, accountId)
      return { baseUrl: account.baseUrl, account: account.username, password }
    },
  }
}
