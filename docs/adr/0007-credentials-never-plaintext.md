# ADR-0007：凭据全程密文，明文只在内存（含已知风险窗口）

- **状态**：已采纳（阶段 2A）—— **含一处已登记的未修风险**（见「已知风险」）
- **相关**：[SCHEMA.md](../SCHEMA.md) §3.5/§5.2、[EXTENSION-PLANE.md](../EXTENSION-PLANE.md) §4.1/§8.2、
  [ADR-0004](0004-extension-as-execution-plane.md)、[API.md](../API.md) §8

## 背景

本项目要存储并自动使用两类敏感值：

1. **平台账号口令**（统一账号库，`account.password_enc`）——用于自动登录与自动填表；
2. **DeepSeek API Key**（`settings.apiKey`）——用于对话与蓝图生成。

同时存在四条凭据通道：桌面 UI 录入 → SQLite；桌面 → 扩展（快照下发）；备份文件导出/导入；
以及原生 HTTP 客户端（`egmp.client.login`）与 Playwright 自动登录的直接使用。

风险面很实在：**凭据丢失不可重建**（[ADR-0001](0001-sqlite-over-mysql-redis.md) 把它作为排除 Redis 的首要理由），
且交付形态是单机安装给每个人——用户的笔记本、共享盘、故障报告都可能成为泄漏路径。

## 决策

**任何通道都不得落明文；明文只在内存中出现，且停留时间尽可能短。**

### 1. 静态存储：Fernet（AES-128-CBC + HMAC-SHA256）

- 密钥为随机 32 字节 urlsafe base64，存 `settings.account_fernet_key`（同库不同表）。
- `password_enc` 存 Fernet 密文；读写入口只有 `accounts.encrypt_password` / `decrypt_password`。
- **键位规范（实锤断点，勿改）**：`sign-key = key[0:16]`（HMAC）、`enc-key = key[16:32]`（AES-CBC）。
  **写反会导致扩展端全员解密失败**（0.2.6 实锤）。
- 接口层永不回传密文或明文：`accounts._sanitize()` **剔除 `password_enc`**，只留 `has_password: bool`。

### 2. 桌面 → 扩展：`密文 + 密钥` 经本机回环下发

理由是**扩展端不能持有 Fernet 密钥的持久副本**，也不能让桌面把密钥写进扩展存储：

```
GET /extension/snapshot
  → { fernetKey: <32B urlsafe b64>, accounts:[{..., passwordEnc: <token>}] }
  → 扩展 WebCrypto 内存解密（HMAC verify → AES-CBC decrypt）
  → 明文仅存在于该函数栈与 parallelStore 调用参数中
  → 立即交 credentials.encryptCredentials()（AES-GCM，设备绑定种子）落盘
```

- **WebCrypto AES-CBC 自动去 PKCS7 填充**——解密结果**不可**再按尾字节手工剥离，
  否则会把口令尾字符当填充长度剥掉（曾把 `"88888888"` 剥成空串，0.2.6 实锤断点之二）。
- 解密失败**必须留痕**（`console.warn` 后跳过该账号）：静默跳过会让「密钥轮换后全员停更」毫无迹象。

### 3. 备份文件：密钥随文件走

`export_backup()` 输出 `fernetKey` + 全部密文口令（原 quick-login 的 `cryptoSeed` 语义）。
导入时用**文件内密钥**解密 → 用**本地密钥**重加密落库 → 同站同名去重。
无法用文件密钥解开的条目**跳过并计数**（损坏/篡改/密钥不符），不整体失败。

> ⚠️ 备份文件本身即凭据。接口文档与 UI 都必须在导出路径上明确提示用户自行保管。

### 4. 使用侧：明文的生命周期

| 使用点 | 明文来源 | 生命周期 |
|---|---|---|
| 原生 HTTP 登录（`egmp.client.login`） | `accounts.reveal_password()` | 函数内 → 立即换取 JWT → **JWT 缓存 55 分钟**（token 缓存比口令更安全地复用） |
| Playwright 自动登录 | `accounts.reveal_password()` | 作为引擎启动参数 → 注入 MAIN world 引擎状态机（`window.__aksoAutoLoginState`） |
| 扩展自动填表 | 快照解密 | 见下方「已知风险」的 60s 窗口 |

### 5. 文档中不得出现明文

任何文档（含本 ADR）举例一律用占位或掩码值。审计语料的示例同样只写形态，不写真实值。

## 已知风险（已登记，未修）

> 这两条属于**扩展侧的产品级隐患**，用户已知悉并暂挂；代码修复需另开一轮（见 [AGENT.md](../../AGENT.md) §4）。

### R1 明文凭据 60s 投递窗口（`getPendingAutoLogin` 读后不删）

扩展把「待自动登录凭证」写在 `chrome.storage.session['sb:pendingAutoLogins:<tabId>']`，
**读取后不删除**。上游 3.13.2 复核仍未修。后果：窗口期（设计约 60s）内，
任何能读 `chrome.storage.session` 的上下文都可拿到**明文口令**。
处置选项：读取即删（改为一次性投递）、或改为「按需解密 + 短 TTL + 用后清除」。

### R2 同步通道无认证

`/extension/*` 与 `/api/*` 全部无凭据校验。本机任意进程都能：
拉快照（**拿到 Fernet 密钥 + 全部密文口令**）、下发指令（触发开设任意账号的浏览器页）、
读 `/api/accounts/export`（直接拿备份文件等价物）。
现有信任边界是「仅监听 127.0.0.1 + 单用户机器」，见 [API.md](../API.md) §0。
若将来要支持多用户共用一台机器，这条必须优先解决（最小改动：回环 token + Origin 校验）。

### R3 声明式全站权限

manifest `host_permissions: ["<all_urls>"]`（0.2.21 起）——权限面比功能所需更宽，
换来的是免除逐站点授权流程。这是一次**有意的可用性取舍**，但要在安全评审时被知悉。

## 后果

- `account_fernet_key` 一旦丢失（settings 表损坏/被清理），**全部口令不可恢复**——
  因此它必须和 `account.password_enc` 同库同事务边界内存活；备份文件内嵌密钥正是为此。
- **密钥轮换是本项目尚未实现的运维动作**：轮换需要重写全部 `password_enc` 并通知扩展重新同步。
  当前若手工改 `account_fernet_key`，表现是接口能读、扩展静默丢弃全部账号（有 `console.warn` 留痕）。
- 审计与日志纪律：`agent_audit.args` 目前会原样存工具入参（截断 2,000 字符）。
  **凡是把凭据作为工具入参的调用都会落库**——见 [AGENT.md](../../AGENT.md) §4 的待修项。
- 真机验证入口：`tools/diag_fernet_node.mjs`（跨语言 Fernet 一致性）、
  `tests/test_accounts.py::test_fernet_roundtrip` / `test_fernet_unique_ciphertexts`（同明文产生不同密文）。
