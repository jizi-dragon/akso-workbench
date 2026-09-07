"""钉钉知识库增量同步器：树 diff → 变更拉取 → 切块 → 哈希去重 → 墓碑。

对应《钉钉知识库-Agent交互提升方案》D2：
- 增量判定：wiki 树节点带 updateTime，与本地 manifest 对比，只重读变更文档
- 块级去重：chunk_hash（SHA-256 前 32 位）——内容未变的块不重嵌，成本降 80~95%
- 墓碑机制：文档消失/块内容变更 → valid_to 置失效（防幽灵 chunk，保留审计）
- 限流保护：ddkb 层节奏控制 + 退避重试；本层逐文档容错（单篇失败不断全量）
- 自检：抽样验证"本次更新的文档在本地检索可召回"（新鲜度验收）
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field

from . import chunking, ddkb, retrieval, storage
from .settings import load_embedding

logger = logging.getLogger("mogul.sync")

EMBED_BATCH = 12
SELF_CHECK_SAMPLES = 3
SYNCED_KEY = "dingtalkLastSync"


@dataclass
class SyncReport:
    startedAt: int
    finishedAt: int = 0
    durationMs: int = 0
    spaces: list = field(default_factory=list)
    docsTotal: int = 0
    docsNew: int = 0
    docsUpdated: int = 0
    docsUnchanged: int = 0
    docsRemoved: int = 0
    docsFailed: int = 0
    chunksInserted: int = 0
    chunksKept: int = 0
    chunksInvalidated: int = 0
    embeddedChunks: int = 0
    selfCheck: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def chunk_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]


async def sync_dingtalk(
    workspace_ids: list[str] | None = None, max_docs: int | None = None
) -> dict:
    """执行一次增量同步，返回同步报告（dict）。阻塞调用已用 to_thread 包装。"""
    report = SyncReport(startedAt=storage.now_ms())
    started = time.perf_counter()
    emb = load_embedding()

    # 1. 空间列表（可按 workspaceIds 过滤）
    spaces = await asyncio.to_thread(ddkb.wiki_spaces)
    if workspace_ids:
        wanted = set(workspace_ids)
        spaces = [s for s in spaces if s.get("workspaceId") in wanted]
    report.spaces = [
        {"workspaceId": s.get("workspaceId"), "name": s.get("name")} for s in spaces
    ]

    # 2. 全树枚举（单空间失败不影响其他空间）
    files: list[dict] = []
    for space in spaces:
        try:
            nodes = await asyncio.to_thread(ddkb.wiki_tree, space["workspaceId"])
            files.extend({**node, "workspaceId": space["workspaceId"]} for node in nodes)
        except Exception as exc:  # noqa: BLE001
            report.errors.append({"stage": "tree", "space": space.get("name"), "error": str(exc)})
            logger.warning("树枚举失败 %s: %s", space.get("name"), exc)
    report.docsTotal = len(files)

    # 3. 墓碑：本地有、树上没有的文档 → 全块失效 + 标记移除
    manifest = {doc["sourceId"]: doc for doc in storage.list_dingtalk_documents()}
    tree_ids = {f"{node['workspaceId']}:{node['nodeId']}" for node in files}
    for source_id, doc in manifest.items():
        if source_id not in tree_ids and doc.get("sourceUpdatedAt") != -1:
            storage.invalidate_doc_chunks(doc["id"])
            storage.mark_document_removed(doc["id"], storage.now_ms())
            report.docsRemoved += 1

    # 4. 变更判定：updateTime 与本地一致 → 跳过
    todo: list[dict] = []
    for node in files:
        source_id = f"{node['workspaceId']}:{node['nodeId']}"
        old = manifest.get(source_id)
        if old and old.get("sourceUpdatedAt") == node.get("updateTime"):
            report.docsUnchanged += 1
            continue
        todo.append(node)
    if max_docs is not None:
        todo = todo[:max_docs]

    # 5. 逐文档拉取 + 块级哈希去重入库
    updated_samples: list[dict] = []
    for node in todo:
        source_id = f"{node['workspaceId']}:{node['nodeId']}"
        try:
            data = await asyncio.to_thread(ddkb.doc_read, node.get("docUrl") or node["nodeId"])
            markdown = data.get("markdown") or ""
            saved = storage.upsert_dingtalk_document(
                source_id, node.get("name") or data.get("title") or "未命名",
                markdown, node.get("docUrl") or "", node.get("updateTime") or 0,
                storage.now_ms(),
            )
            result = _rechunk(saved["id"], markdown)
            report.chunksInserted += result["inserted"]
            report.chunksKept += result["kept"]
            report.chunksInvalidated += result["invalidated"]
            if saved["isNew"]:
                report.docsNew += 1
            else:
                report.docsUpdated += 1
            if emb.ready and result["newChunkRows"]:
                report.embeddedChunks += await _embed_chunks(result["newChunkRows"], emb)
            if len(updated_samples) < SELF_CHECK_SAMPLES and result["inserted"]:
                updated_samples.append({"name": node.get("name") or "", "content": markdown})
        except Exception as exc:  # noqa: BLE001 —— 单篇失败不断全量
            report.docsFailed += 1
            report.errors.append({"stage": "doc", "node": node.get("name"), "error": str(exc)})
            logger.warning("文档同步失败 %s: %s", node.get("name"), exc)

    # 6. 新鲜度自检（D4）
    report.selfCheck = await _self_check(updated_samples)

    report.finishedAt = storage.now_ms()
    report.durationMs = int((time.perf_counter() - started) * 1000)
    payload = report.to_dict()
    storage.set_setting(SYNCED_KEY, json.dumps(payload, ensure_ascii=False))
    logger.info(
        "钉钉同步完成：新增 %d / 更新 %d / 不变 %d / 移除 %d / 失败 %d，块 +%d ~%d 嵌入 %d，耗时 %dms",
        report.docsNew, report.docsUpdated, report.docsUnchanged, report.docsRemoved,
        report.docsFailed, report.chunksInserted, report.chunksInvalidated,
        report.embeddedChunks, report.durationMs,
    )
    return payload


def _rechunk(doc_id: str, markdown: str) -> dict:
    """块级哈希去重：保留哈希未变的旧块（不重嵌），失效消失的哈希，插入新哈希。"""
    old = {row["chunkHash"]: row["id"] for row in storage.list_valid_chunk_hashes(doc_id)}
    chunks = chunking.split_into_chunks(markdown)

    new_hashes: list[str] = []
    inserts: list[tuple[int, str, str]] = []
    kept = 0
    seen: set[str] = set()
    for index, content in enumerate(chunks):
        digest = chunk_hash(content)
        new_hashes.append(digest)
        if digest in old:
            kept += 1
            continue
        if digest in seen:
            continue
        seen.add(digest)
        inserts.append((index, content, digest))

    valid_new = set(new_hashes)
    invalidated = storage.invalidate_doc_chunks(doc_id, [h for h in old if h not in valid_new])
    inserted_rows = storage.insert_dingtalk_chunks(doc_id, inserts)
    return {
        "inserted": len(inserted_rows),
        "kept": kept,
        "invalidated": invalidated,
        "newChunkRows": inserted_rows,
    }


async def _embed_chunks(chunks: list[dict], emb) -> int:
    from .embedding import embed_texts  # 局部导入避免包初始化顺序问题

    embedded = 0
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start : start + EMBED_BATCH]
        try:
            vectors = await embed_texts([c["content"] for c in batch], emb)
        except Exception as exc:  # noqa: BLE001 —— 向量化失败不影响精确/关键词检索
            logger.warning("块向量化失败（可稍后重建索引）：%s", exc)
            return embedded
        for chunk, vector in zip(batch, vectors, strict=False):
            storage.update_chunk_embedding(chunk["id"], vector)
            embedded += 1
    return embedded


def _probe_term(content: str) -> str | None:
    """取正文里一段 8~24 位连续中文作为检索探针（跳过标题行）。"""
    for line in content.split("\n")[1:]:
        text = line.strip().lstrip("#|*- ")
        run = ""
        for ch in text:
            if "\u4e00" <= ch <= "\u9fff":
                run += ch
                if len(run) >= 8:
                    return run[:24]
            else:
                run = ""
    return None


async def _self_check(samples: list[dict]) -> dict:
    """新鲜度自检：用刚同步文档的正文片段查本地检索，应能召回该文档内容。"""
    checked = passed = 0
    for sample in samples:
        term = _probe_term(sample["content"])
        if not term:
            continue
        checked += 1
        result = await retrieval.search_knowledge(
            term, storage.list_chunks(), None,
        )
        if any(term in hit.get("content", "") for hit in result["hits"]):
            passed += 1
    return {"checked": checked, "passed": passed}


async def run_scheduled() -> dict | None:
    """定时同步入口（由应用 lifespan 周期调用）：检查开关与间隔，到期才执行。"""
    if storage.get_setting("dingtalkSyncEnabled") != "true":
        return None
    try:
        interval_hours = float(storage.get_setting("dingtalkSyncIntervalHours") or 6)
    except ValueError:
        interval_hours = 6.0
    last_raw = storage.get_setting(SYNCED_KEY)
    last_finished = 0
    if last_raw:
        try:
            last_finished = int(json.loads(last_raw).get("finishedAt") or 0)
        except ValueError:
            last_finished = 0
    if storage.now_ms() - last_finished < interval_hours * 3_600_000:
        return None
    try:
        return await sync_dingtalk()
    except Exception as exc:  # noqa: BLE001 —— 定时失败不影响应用
        logger.error("定时同步失败：%s", exc)
        return None
