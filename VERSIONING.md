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
