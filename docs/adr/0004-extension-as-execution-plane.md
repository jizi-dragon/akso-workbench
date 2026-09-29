# ADR-0004：浏览器扩展只做执行面（数据面归桌面）

- **状态**：已采纳（0.2.23 定稿，净删 2,849 行）
- **决策者**：用户定稿（「不影响功能」）
- **相关**：`../EXTENSION-PLANE.md`（**该文件已于 2026-09-29 删除**）、[ADR-0002](0002-browser-allocation-policy.md)、
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
- ~~上游同步的冲突面缩小：本项目只需维护一份「私有改造清单」~~（见
  [extensions/quick-login/CHANGELOG.md](../../extensions/quick-login/CHANGELOG.md)）。
  **2026-09-29 后补**：上游协作已终止，**"同步冲突面"这个收益随之消失** —— 改造清单仍然要维护，
  但它现在服务的读者是**本项目**（"哪些改动别误删"），不再是"下次同步上游要摘哪些"。

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
  `../EXTENSION-PLANE.md`（**该文件已于 2026-09-29 删除**） §4。
- **四条实锤护栏**（都源于这次收敛之后暴露的问题，改动 `sync.ts` 前必读）：
  ① `snapshotId` 幂等；② 空快照护栏（否则桌面瞬时半量快照会删光本地账号）；
  ③ `consuming` 互斥（防同一 `par.open` 开两个页签）；④ `streaming` 幂等 + `longPoll` 退避（防热循环）。
- **关联决策**：扩展的凭据投递语义（密文 + 密钥经回环下发）见
  [ADR-0007](0007-credentials-never-plaintext.md)——注意它的**已知风险窗口**（明文凭据 60s 投递窗口读后不删）。

---

## 外部参照（2026-09-20 后补）

> ⚠️ **本栏为后补**（ADR 正文其余部分保持决策当时的样子，不改写）。
> 补它的动机很直接：**这条决策的价值几乎全在"我们为何不同于别人"**，
> 而那部分在正文里只写了结论、没写对照对象。半年后没人能重建当时的权衡。

| 参照对象 | 它的做法 | 与本决策的关系 | 我们的理由 |
|---|---|---|---|
| [nanobrowser](https://github.com/nanobrowser/nanobrowser)（约 13,810 ★） | **MV3 扩展即全部**：agent 循环、工具、账号都在扩展里，**没有宿主后端**；不做多账号存储平面隔离 | **分歧** | 我们需要承载「业务写路径 + 审计 + 凭据库」，而扩展没有这些载体（无 SQLite、无 Fernet 密钥管理、无法跑平台 SDK）。**数据面放桌面不是为了架构好看，是因为扩展装不下** |
| [browser-use](https://github.com/browser-use/browser-use) / [Skyvern](https://github.com/Skyvern-AI/skyvern) / [Stagehand](https://github.com/browserbase/stagehand) | 一律用**受控的浏览器上下文**（可弃、无登录态），因此**不需要**存储平面隔离 | **分歧** | 我们的执行面是**用户自己已登录的真实 Chrome**——这是本项目的核心前提（真实登录态不复制、凭据不出本机）。隔离问题正是这个前提的**代价**，不是可选项 |
| [camoufox](https://github.com/daijro/camoufox) / [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) | 解决**跨站点指纹一致性 / 反检测** | **正交** | 我们要的是**同站点内多账号的存储平面隔离**，与指纹无关。且本项目的用户是合法持有这些账号的实施工程师——**刻意不做反检测**（合规取向） |
| Multilogin / AdsPower 一类商用多账号浏览器 | 商业闭源 | **无开源对应物** | 本次调研（`../OPENSOURCE-LANDSCAPE.md`（**该文件已于 2026-09-29 删除**） §2.7）**未找到**六个平面同时显式隔离 + 会话切换 + 回放的开源实现。⚠️ 该结论**限于检索范围，非穷尽性证明** |

**由本次对照得出的结论**：

- **这条决策没有现成共识可抄。** 行业里「扩展 = 执行面 + 桌面 = 数据面」没有同构体，
  六平面隔离也没有开源对应物。所以它不是"跟上行业"，而是**在自己的约束下自己定的**——
  这正是它值得被记成 ADR 的原因。
- **必须自己盯住的部分**：正文「代价」里那条"跨进程协议成为关键路径"。
  行业参照物都没有这条协议，因此**我们无法从别人那里学到它的坑**——
  只能靠自己的 PITFALLS #14 / #15 / #48 / #49 一条条攒。
- **可以放心的部分**：决策本身的形状（单一真源、职责二分）是通用的工程常识，不必自证。

