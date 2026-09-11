# ADR-0004：浏览器扩展只做执行面（数据面归桌面）

- **状态**：已采纳（0.2.23 定稿，净删 2,849 行）
- **决策者**：用户定稿（「不影响功能」）
- **相关**：[EXTENSION-PLANE.md](../EXTENSION-PLANE.md)、[ADR-0002](0002-browser-allocation-policy.md)、
  [ADR-0007](0007-credentials-never-plaintext.md)、[SCHEMA.md](../SCHEMA.md) §5

## 背景

`extensions/quick-login/` 是上游 quick-login 项目的 fork。上游是**纯浏览器形态**：
扩展自带并行管理主页 `ui/parallel/`，用户在扩展里直接增删改账号，账号档案存在扩展的 IndexedDB。

本项目 fork 之后，账号数据的真源移到了桌面端（SQLite + Fernet），于是出现**两套账号数据**：

- 桌面 `account` 表（账号中心 UI 在改）；
- 扩展 IndexedDB `accounts`（上游 UI 在改，且本项目已把并行页隐藏）。

后果是双向的：两边都能改却没有对账协议，且上游那套「会话模型」——`session-manager.ts` /
`account-registry.ts` / `navigation.ts` + `session.*` 消息协议——与桌面端的 `par.open` 语义重叠，
留下来只会让人不知道该走哪条路。

## 决策

**扩展 = 执行面（Execution Plane），数据面（Control/Data Plane）归桌面。**

| 归属 | 内容 |
|---|---|
| **扩展负责** | ① 六平面隔离（存储 / AUTH / COOKIE / CACHE / SW·CacheStorage / IndexedDB）；② 会话切换 `par.open`；③ 登录表单自动填表；④ 轮盘呼出 `wheel.toggle`；⑤ 状态上报；⑥ 在 `sync.ts` 里对账桌面快照 |
| **桌面负责** | 账号 / 盒子 / 站点的全部增删改；凭据 Fernet 加密存储；托管浏览器与监听录制；全部 UI 入口 |

具体动作：

1. **删**：死文件 `ui/parallel/*`（1,290+130+629 行）与 `ui/send.ts`；旧会话模型三件套 +
   `session.*` 协议 + `Session` 类型 + `sessionTabBindings`；并行页专用协议
   `par.create/update/delete/moveBox/renameBox/deleteBox/probeScheme` + `data.export/import`（SW 死分发约 275 行）。
   合计 **净删 2,849 行 / 18 文件**。
2. **留（别误删）**：`parallelStore` 的增删改（`sync.ts` 直接调用 = 活数据面）；
   `site-auth.ts` + `site.grants.*` + `par.grantChanged`（0.2.21 定稿保留的授权/停用核心，
   现状为**休眠源码**）；`ql.diag`（诊断）；`wheel.toggle`（桌面通道）。
3. **单一真源**：协议键只在 `shared/constants.ts` 定义；host/端口五函数收归 `background/core/host.ts`；
   `applyTitle` → `tabs/tab-title.ts`；待登录凭证 → `core/pending-login.ts`。

## 取舍

### 换来什么

- **数据只有一个真源**（桌面 SQLite），消除「两边都能改」的分裂。
- 扩展的职责边界变得可验证：`sync.ts` 是唯一入口，实体是快照 + 映射表（`akso:acctMap`）。
- 上游同步的冲突面缩小：本项目只需维护一份「私有改造清单」（见
  [extensions/quick-login/CHANGELOG.md](../../extensions/quick-login/CHANGELOG.md)）。

### 代价（已接受）

- **扩展脱离桌面不可用**：账号数据不再能在扩展里自建。桌面不可达时扩展只能跑本地已有数据
  （「离线回退」：`getJson` 返回 `null` 即静默跳过，本地账号仍可用）。
- **跨进程协议成为关键路径**：快照幂等、指令游标、状态上报三件套一旦出问题就是**静默失效**
  （不报错、只是不动）。因此协议契约被写进 [SCHEMA.md](../SCHEMA.md) §5 与 [API.md](../API.md) §11/§12.3。
- **上游同步要反复摘除**：`ui/parallel` 与 Page Monitor 之类会在上游新版里回来，
  必须按清单再删一次（清单已落 CHANGELOG）。
- **休眠源码的技术债**：`site-auth.ts` 保留但不参与现行授权链路（0.2.21 改声明式全站权限后
  只剩「用户手动停用名单」语义），属于「保留以防回退」而非「正在使用」。

## 后果

- 协议三面（数据/指令/状态）的语义与护栏成为扩展侧最需要读的代码路径，已固化为
  [EXTENSION-PLANE.md](../EXTENSION-PLANE.md) §4。
- **四条实锤护栏**（都源于这次收敛之后暴露的问题，改动 `sync.ts` 前必读）：
  ① `snapshotId` 幂等；② 空快照护栏（否则桌面瞬时半量快照会删光本地账号）；
  ③ `consuming` 互斥（防同一 `par.open` 开两个页签）；④ `streaming` 幂等 + `longPoll` 退避（防热循环）。
- **关联决策**：扩展的凭据投递语义（密文 + 密钥经回环下发）见
  [ADR-0007](0007-credentials-never-plaintext.md)——注意它的**已知风险窗口**（明文凭据 60s 投递窗口读后不删）。
