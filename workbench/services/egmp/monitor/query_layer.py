"""Monitor 查询层 + 参数推断（query-layer.js / param-extractor.js / param-comparator.py 的 Python 化）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

READ_METHODS = {"GET"}
WRITE_HINT = re.compile(r"Save|Create|Update|Delete|Add|Submit|Change|Remove|Import", re.IGNORECASE)
DANGEROUS_HINT = re.compile(r"Delete|Remove|ChangeStatus|Disable|Cancel", re.IGNORECASE)


def classify_api(method: str, path: str) -> str:
    """READ / WRITE / DANGEROUS（param-validator.js 三级：DANGEROUS 全跳过回放）。"""
    if DANGEROUS_HINT.search(path):
        return "DANGEROUS"
    if method.upper() in READ_METHODS:
        return "READ"
    if WRITE_HINT.search(path):
        return "WRITE"
    return "READ" if method.upper() == "GET" else "WRITE"


# ------------------------------------------------------------ 日志读取（三种格式兼容）

def read_log_entries(path: Path) -> list[dict[str, Any]]:
    """兼容：纯数组 / {entries,_actions} / 旧版多段拼接（取最后一块）。"""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            return data["entries"]
    except ValueError:
        pass
    # 多段拼接：逐段尝试，取能解析的最后一块
    best: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        chunk_start = text.find("{", idx)
        if chunk_start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[chunk_start:])
            if isinstance(obj, dict) and isinstance(obj.get("entries"), list):
                best = obj["entries"]
            idx = chunk_start + end
        except ValueError:
            idx = chunk_start + 1
    return best


def query_monitor_log(path: Path, *, method: str | None = None, keyword: str | None = None,
                      unknown_only: bool = False) -> dict[str, Any]:
    entries = read_log_entries(path)
    filtered = []
    for entry in entries:
        if method and entry.get("method", "").upper() != method.upper():
            continue
        if keyword and keyword.lower() not in str(entry.get("path", "")).lower():
            continue
        if unknown_only and entry.get("known"):
            continue
        filtered.append(entry)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in filtered:
        key = f"{entry.get('method')} {entry.get('path')}"
        grouped.setdefault(key, []).append(entry)
    return {"summary": {key: len(rows) for key, rows in grouped.items()},
            "groups": grouped, "total": len(filtered)}


# ------------------------------------------------------------ 参数提取（JSON Path 扁平化）

def _flatten(obj: Any, prefix: str = "$", depth: int = 0, max_depth: int = 10) -> dict[str, str]:
    flat: dict[str, str] = {}
    if depth > max_depth:
        flat[f"{prefix}__truncated"] = type(obj).__name__
        return flat
    if isinstance(obj, dict):
        for key, value in obj.items():
            flat.update(_flatten(value, f"{prefix}.{key}", depth + 1, max_depth))
    elif isinstance(obj, list):
        if not obj:
            flat[prefix] = "[]"
        for idx, item in enumerate(obj[:20]):
            flat.update(_flatten(item, f"{prefix}[{idx}]", depth + 1, max_depth))
    elif isinstance(obj, bool):
        flat[prefix] = "boolean"
    elif isinstance(obj, (int, float)):
        flat[prefix] = "number"
    elif obj is None:
        flat[prefix] = "null"
    else:
        flat[prefix] = "string" if len(str(obj)) <= 200 else "longtext"
    return flat


def extract_params(post_body: Any) -> dict[str, str]:
    if not post_body:
        return {}
    if isinstance(post_body, str):
        try:
            post_body = json.loads(post_body)
        except ValueError:
            return {"$raw": "string"}
    if not isinstance(post_body, (dict, list)):
        return {"$raw": type(post_body).__name__}
    return _flatten(post_body)


def extract_from_log(path: Path, api_path: str) -> dict[str, dict[str, str]]:
    """按 API 路径取样并合并：path → {json_path: type}。"""
    entries = [e for e in read_log_entries(path) if e.get("path") == api_path]
    merged: dict[str, dict[str, str]] = {}
    for entry in entries:
        params = extract_params(entry.get("postData"))
        for key, value_type in params.items():
            merged.setdefault(key, {})
            merged[key][value_type] = merged[key].get(value_type, 0) + 1
    return merged


def compare_recordings(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """多样本推断：出现率/类型一致性/requiredness/enum 候选/置信度（comparator 口径）。"""
    total = len(samples)
    if total == 0:
        return {"params": {}, "confidence": 0, "issues": ["insufficient_samples"]}
    all_paths: set[str] = set()
    flattened = []
    for sample in samples:
        flat = extract_params(sample.get("postData") or sample.get("body"))
        flattened.append(flat)
        all_paths.update(flat)

    params: dict[str, dict[str, Any]] = {}
    for json_path in sorted(all_paths):
        values = [flat.get(json_path) for flat in flattened]
        present = [v for v in values if v is not None]
        occurrence = len(present) / total
        type_counts: dict[str, int] = {}
        for value in present:
            type_counts[value] = type_counts.get(value, 0) + 1
        dominant = max(type_counts, key=lambda k: type_counts[k]) if type_counts else "unknown"
        consistency = type_counts.get(dominant, 0) / len(present) if present else 0
        entry: dict[str, Any] = {
            "occurrenceRate": round(occurrence, 3),
            "dominantType": dominant,
            "typeConsistency": round(consistency, 3),
            "required": occurrence == 1.0,
            "enumCandidate": len({json.dumps(flattened[i].get(json_path)) for i in range(total)
                                  if flattened[i].get(json_path) is not None}) <= 10
            and dominant not in {"longtext"},
        }
        params[json_path] = entry

    base_confidence = (sum(p["occurrenceRate"] * p["typeConsistency"]
                           for p in params.values()) / len(params)) if params else 0
    issues = []
    type_conflicts = [p for p, v in params.items() if v["typeConsistency"] < 1.0]
    if type_conflicts:
        issues.append({"kind": "type_conflict", "params": type_conflicts})
    return {"params": params, "confidence": round(base_confidence * 100, 1), "issues": issues}
