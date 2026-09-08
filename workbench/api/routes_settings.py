"""引导数据 + 设置路由。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..services import storage
from ..services.settings import all_settings_json, save_deepseek

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsBody(BaseModel):
    apiKey: str | None = None
    model: str | None = None
    temperature: float | None = None


@router.get("/bootstrap")
def bootstrap() -> dict:
    metrics = storage.list_metrics(200)
    rated = [m for m in metrics if m["feedback"] is not None]
    adopted = [m for m in rated if m["feedback"] == 1]
    return {
        "conversations": storage.list_conversations(),
        "stats": {
            "qaTotal": len(metrics),
            "adoptRate": round(len(adopted) / len(rated) * 100) if rated else None,
        },
        "settings": all_settings_json(),
    }


@router.put("/settings")
def put_settings(body: SettingsBody) -> dict:
    save_deepseek(
        api_key=body.apiKey,
        model=body.model,
        temperature=body.temperature,
    )
    return {"ok": True, "settings": all_settings_json()}
