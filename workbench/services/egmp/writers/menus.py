"""菜单创建（akso-auto create-menu.js 的 Python 化）。"""

from __future__ import annotations

from typing import Any

from .endpoints import MENU_ACTION_TYPE, MENU_SORT, MENU_SUBMIT, MENU_TYPE, MENUGROUP_QUERY


def get_menu_groups(client: Any) -> list[dict[str, Any]]:
    return list(client.post(MENUGROUP_QUERY, {}) or [])


def create_parent_menu(client: Any, *, name: str, code: str,
                       background_color: str = "#366EF4") -> dict[str, Any]:
    """母菜单（容器，不关联对象）。"""
    resp = client.post(MENU_SUBMIT, {
        "source": 2, "menuType": MENU_TYPE["STANDARD"], "backgroundColor": background_color,
        "status": 1, "name": name, "code": code,
        "menuActionType": MENU_ACTION_TYPE["CONTAINER"], "childShowType": None,
    })
    menu_id = None
    if isinstance(resp, dict):
        menu_id = resp.get("Id") or resp.get("id")
    return {"success": True, "menuId": menu_id, "message": f"母菜单[{name}]创建成功"}


def create_sub_menu(client: Any, *, name: str, code: str, parent_id: str,
                    object_id: str, listlayout_id: str | None = None,
                    form_layout_id: str | None = None) -> dict[str, Any]:
    """子菜单（关联对象 + 列表/表单布局）。"""
    resp = client.post(MENU_SUBMIT, {
        "source": 2, "menuType": MENU_TYPE["STANDARD"], "status": 1,
        "name": name, "code": code, "backgroundColor": "#366EF4",
        "menuActionType": MENU_ACTION_TYPE["OBJECT_LINK"],
        "parentId": parent_id, "basicObjectId": object_id,
        "listlayoutId": listlayout_id, "layoutId": form_layout_id,
    })
    menu_id = None
    if isinstance(resp, dict):
        menu_id = resp.get("Id") or resp.get("id")
    return {"success": True, "menuId": menu_id, "message": f"子菜单[{name}]创建成功"}


def sort_menus(client: Any, tree: list[dict[str, Any]]) -> dict[str, Any]:
    """全量树替换排序。"""
    client.post(MENU_SORT, {"menus": tree})
    return {"success": True, "message": "菜单排序成功"}
