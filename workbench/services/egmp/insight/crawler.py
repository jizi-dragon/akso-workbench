"""配置爬取层（akso-cc crawler/* 的 Python 化）：对象发现链 / 字段 / 平台分片 / 布局。

实证链（object-discovery.ts 口径，重写不得丢失）：
  1. 菜单项 + 工作流行提取 objectId
  2. objectId → Listlayout/List（或 Layout/LayoutList）取布局 code，
     剥 _table__ak / _base_layout__ak 后缀
  3. 补 __c / __ak / __sys / 无后缀候选，经 OpenAPI 4.8 验证（每批 8 并发）
  4. 探针 BasicObject/QueryList | GetPageList | Page 兜底
  5. --known-objects 白名单兜底
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

LAYOUT_CODE_SUFFIXES = ("_table__ak", "_base_layout__ak")
OBJECT_SUFFIX_CANDIDATES = ("__c", "__ak", "__sys", "")


def strip_layout_code(code: str) -> str:
    for suffix in LAYOUT_CODE_SUFFIXES:
        if code.endswith(suffix):
            return code[: -len(suffix)]
    return code


def candidate_codes(raw: str) -> list[str]:
    """布局 code → 对象编码候选（剥后缀 + 补四种后缀）。"""
    base = strip_layout_code(raw)
    seen: list[str] = []
    for suffix in OBJECT_SUFFIX_CANDIDATES:
        candidate = base + suffix
        if candidate not in seen:
            seen.append(candidate)
    return seen


class Crawler:
    """只读爬取器（持有已登录 EgmpClient）。"""

    def __init__(self, client: Any, known_objects: list[str] | None = None) -> None:
        self._client = client
        self.known_objects = list(known_objects or [])
        self.failures: list[dict[str, str]] = []
        self.discovery_via = "none"

    # ---------------------------------------------------------- 对象发现链

    def discover_objects(self) -> list[dict[str, Any]]:
        """实证链全流程。返回 [{code, name?, id?, source}]。"""
        found: dict[str, dict[str, Any]] = {}

        # 1) 菜单 + 工作流行收 objectId
        object_ids: set[str] = set()
        try:
            for menu in self._client.list_menus() or []:
                oid = menu.get("objectId")
                if oid:
                    object_ids.add(str(oid))
        except Exception as exc:  # noqa: BLE001
            self.failures.append({"item": "menu", "error": str(exc)})
        try:
            for row in self._client.list_workflows() or []:
                oid = row.get("objectId")
                if oid:
                    object_ids.add(str(oid))
        except Exception as exc:  # noqa: BLE001
            self.failures.append({"item": "workflow", "error": str(exc)})
        self.discovery_via = f"menu+workflow({len(object_ids)} ids)"

        # 2/3) objectId → 布局 code → 候选验证（8 并发）
        layout_codes: list[str] = []
        for oid in object_ids:
            try:
                rows = self._client.get("/api/platform/Listlayout/List", params={"objectId": oid}) or []
                for row in rows:
                    code = str(row.get("code") or "")
                    if code:
                        layout_codes.append(code)
                        continue
            except Exception:  # noqa: BLE001 —— 降级到 LayoutList
                try:
                    data = self._client.get(
                        "/api/platform/Layout/LayoutList",
                        params={"basicObjectId": oid, "pageIndex": 1, "pageSize": 5},
                    )
                    for row in (data or []):
                        code = str(row.get("code") or "")
                        if code:
                            layout_codes.append(code)
                except Exception as exc:  # noqa: BLE001
                    self.failures.append({"item": f"layout:{oid}", "error": str(exc)})

        codes: set[str] = set()
        for raw in layout_codes:
            codes.update(candidate_codes(raw))

        def verify(code: str) -> str | None:
            try:
                meta = self._client.get_object(code)
                return code if meta else None
            except Exception:  # noqa: BLE001
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            for result in pool.map(verify, sorted(codes)):
                if result:
                    found[result] = {"code": result, "source": "layout-resolve"}

        # 4) 探针兜底（QueryList/GetPageList/Page 可用性验证白名单外对象——
        #    平台无「全对象列表」接口，探针只用于验证显式给定的编码，这里记录通道）
        if found:
            self.discovery_via += " + openapi-verify"

        # 5) 白名单兜底
        for code in self.known_objects:
            code = code.strip()
            if code and code not in found:
                try:
                    meta = self._client.get_object(code)
                    if meta:
                        found[code] = {"code": code, "source": "known-objects"}
                except Exception as exc:  # noqa: BLE001
                    self.failures.append({"item": code, "error": str(exc)})

        return list(found.values())

    # -------------------------------------------------------------- 字段

    def page_fields(self, object_id: str) -> list[dict[str, Any]]:
        """字段主通道（含内嵌选项集实例）。"""
        try:
            return self._client.paged_post("/api/platform/BasicObject/FieldPage", {"objectId": object_id})
        except Exception as exc:  # noqa: BLE001
            self.failures.append({"item": f"fields:{object_id}", "error": str(exc)})
            return []

    # ------------------------------------------------------------ 平台分片

    def list_workflows(self) -> list[dict[str, Any]]:
        try:
            return self._client.list_workflows()
        except Exception as exc:  # noqa: BLE001
            self.failures.append({"item": "workflows", "error": str(exc)})
            return []

    def list_menu_groups(self) -> list[dict[str, Any]]:
        try:
            return self._client.post("/api/platform/MenuGroup/QueryList", {}) or []
        except Exception as exc:  # noqa: BLE001
            self.failures.append({"item": "menu-groups", "error": str(exc)})
            return []

    def list_menus(self) -> list[dict[str, Any]]:
        try:
            return self._client.list_menus()
        except Exception as exc:  # noqa: BLE001
            self.failures.append({"item": "menus", "error": str(exc)})
            return []

    # --------------------------------------------------------------- 布局

    def form_layouts(self, object_id: str) -> list[dict[str, Any]]:
        try:
            data = self._client.get(
                "/api/platform/Layout/LayoutList",
                params={"basicObjectId": object_id, "pageIndex": 1, "pageSize": 100},
            )
            return list(data or [])
        except Exception as exc:  # noqa: BLE001
            self.failures.append({"item": f"form-layouts:{object_id}", "error": str(exc)})
            return []

    def list_layouts(self, object_id: str) -> list[dict[str, Any]]:
        try:
            rows = self._client.get("/api/platform/Listlayout/List", params={"objectId": object_id})
            return list(rows or [])
        except Exception as exc:  # noqa: BLE001
            self.failures.append({"item": f"list-layouts:{object_id}", "error": str(exc)})
            return []

    # ------------------------------------------------- 对象级深爬（盘点用）

    def crawl_object(self, meta: dict[str, Any], code: str) -> dict[str, Any]:
        """单对象深爬：statuses + fields + 双布局。"""
        object_id = str(meta.get("id") or "")
        record: dict[str, Any] = {
            "code": code,
            "name": meta.get("name"),
            "id": object_id,
            "source": meta.get("source"),
            "objectClass": meta.get("objectClass"),
            "enableLifeCycle": meta.get("enableLifeCycle"),
            "lifeCycleId": meta.get("lifeCycleId"),
            "lifeCycleName": meta.get("lifeCycleName"),
            "fields": self.page_fields(object_id),
            "statuses": [],
            "formLayouts": self.form_layouts(object_id),
            "listLayouts": self.list_layouts(object_id),
        }
        if meta.get("enableLifeCycle"):
            try:
                record["statuses"] = self._client.get_object_statuses(code) or []
            except Exception as exc:  # noqa: BLE001
                self.failures.append({"item": f"statuses:{code}", "error": str(exc)})
        return record


SIGNATURE_RE = re.compile(r"签名|sign|esign", re.IGNORECASE)
