# 版本规则

本仓库与 `akso-pass`、`akso-cloud` 共用同一套版本规则。版本号一律使用 SemVer `MAJOR.MINOR.PATCH`。

## 判定表

| 场景 | 版本变化 | 示例 | 产物 |
|---|---|---|---|
| 日常提交：功能、修复、文档、重构、优化；用户未明确要求打 tag / 发 Release | **PATCH +1**（第三位 +1） | `0.1.0 → 0.1.1`；`0.1.9 → 0.1.10` | 只提交代码与构建本地产物；**不打 tag、不发 Release、不上传安装包** |
| 用户明确要求「打 tag / 打包 release / 发布正式版」 | **MINOR +1**（第二位 +1），PATCH 归零 | `0.1.4 → 0.2.0`；`0.9.1 → 0.10.0` | tag + GitHub Release + 更新下载清单/发布说明 |
| 项目架构发生重大变化，且用户明确要求 | **MAJOR +1**（第一位 +1），MINOR/PATCH 归零 | `0.10.0 → 1.0.0` | 需用户单独确认；不要仅因“改动看起来大”自行升级 |

## 硬规则

- **没有用户明确指令，禁止创建 tag、创建 GitHub Release、上传 ZIP/安装包、修改下载清单。**
- 不要自行把补丁版升级为正式版；也不要因为功能看起来很大就触发 MINOR。
- 日常提交即使是纯文档改动，也按 PATCH +1；同一版本号只对应一次提交。
- 版本号必须在**同一提交**里同步，不能只改 `package.json`：
  - `package.json`
  - `plugin/package.json`
  - `package-lock.json`
  - `plugin/package-lock.json`
  - 任何运行时读取版本的位置
- 推荐流程：
  1. 根目录执行 `npm version <new> --no-git-tag-version`
  2. `plugin/` 目录执行 `npm version <new> --no-git-tag-version`
  3. 检查两个 `package.json` 与两个 lockfile 的版本一致
  4. 跑 `npm run check`
- 插件运行时通过 `plugin/package.json` 读取客户端版本；根 `package.json` 与插件版本必须相同，避免界面/云端看到的版本不一致。
- 发布后的 tag、Release、资产 URL、校验和应写进提交说明或发布说明；没有发布就不写。

## Release 资产只装**构建产物**（2026-11-26 用户口径）

> 用户口径（2026-11-26）：打包成 release 时，上传的必须是**构建后的产物** —— 用户拿走后
> **能直接使用**。此条同样适用于 `akso-pass`。

本仓库发出去的是**DSH 插件包**（不是 Electron 安装器），所以「构建产物」= `plugin/lib/` 下由
`npm run check`（内含 `plugin` 的 `node build.mjs`）产出的两个 bundle，加上插件运行时要读的知识文档。

| 必须包含 | 必须不包含 |
|---|---|
| `plugin/package.json`（host 靠它认 `main`、`dsh.bundle.patch`、版本号） | `plugin/src/**` 的 `.ts`/`.tsx` 源码 |
| `plugin/cordis.patch.yml` | `plugin/test/**`、`node_modules/`、`*.map` |
| `plugin/lib/index.js`、`plugin/lib/client.js`（构建产物） | `_pack/`、`akso-workbench-data/`、`.git/`、两个 `package-lock.json` |
| `docs/knowledge/**`（host 按 mtime 现读；**必须与 `plugin/` 同级**） | 任何本地路径/密钥/会话文件 |
| 一份给最终用户的安装说明（`.md`） | |

- 打包前先跑 `npm run check`（它会重新构建 `plugin/lib/`）；`plugin/package.json` 与根
  `package.json` 的版本必须相同。
- 压缩包解压后根层是包目录（如 `akso-workbench-0.2.13/`），其下直接是 `plugin/` 与 `docs/`。
- **判据**（发出去之前自己过一遍，四条都要过）：
  1. 解压后 `plugin/package.json`、`plugin/lib/index.js`、`plugin/lib/client.js` 都在；
  2. 递归搜不到 `plugin/src/`、`plugin/test/`、`node_modules/`、`*.map`；
  3. `plugin/package.json` 里 `main` 与 `dsh.bundle.patch` 指向的文件都在包里，且 `docs/knowledge/`
     与 `plugin/` 同级；
  4. 在干净的 DSH 里按安装说明装一次：插件页显示与 `package.json` 一致的版本、工具列表可见、
     三份知识文档找得到（`AKSO_WORKBENCH_API_GUIDE` 未设时能按「从插件目录向上 10 级」命中）。

