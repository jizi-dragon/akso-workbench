# 版本管理（已合并进 CONTRIBUTING）

> **2026-10-19：本文已合并并删除正文**，只保留这个指针 —— 历史决策 / PITFALLS 里指向
> `docs/VERSIONING.md` 的链接仍然要能点开，而"旧文里的 tag/Release 数字"已经过期，
> 继续维护它只会制造第二个事实源。
>
> **现行规则**：[CONTRIBUTING.md](../CONTRIBUTING.md) §3.2（`VERSION` 真源与派生副本）＋ §3.6
> （版本三层、tag/Release 纪律、自动更新的公开仓库前提、CHANGELOG 机器接口、发版前检查）。
>
> **取回原文**：`git show 7d5ac5d:docs/VERSIONING.md`（534 行，2026-09-17 的实测记录，
> 其中"12 个版本只有 3 个 tag"等结论**已不再成立**，读的时候按 §3.6 校正）。
>
> 相关：`tools/bump.py`（版本演进）、`tools/build_tauri.ps1`（Tauri 构建）、
> `tools/tauri_update_manifest.py`（内部更新清单）、`CHANGELOG.md`（发布说明）。
