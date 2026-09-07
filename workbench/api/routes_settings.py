"""引导数据 + 设置路由。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..services import embedding, storage
from ..services.settings import all_settings_json, load_embedding, save_deepseek, save_embedding

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsBody(BaseModel):
    apiKey: str | None = None
    model: str | None = None
    temperature: float | None = None


class EmbeddingBody(BaseModel):
    apiKey: str | None = None
    model: str | None = None
    baseUrl: str | None = None


EMBEDDING_TEST_TEXT = "药品生产质量管理规范 GMP 计算机化系统验证"


@router.get("/bootstrap")
def bootstrap() -> dict:
    chunks = storage.list_chunks()
    metrics = storage.list_metrics(200)
    rated = [m for m in metrics if m["feedback"] is not None]
    adopted = [m for m in rated if m["feedback"] == 1]
    documents = storage.list_documents()
    return {
        "conversations": storage.list_conversations(),
        "stats": {
            "documents": len(documents),
            "chunks": len(chunks),
            "indexed": sum(1 for c in chunks if c.get("embedding")),
            "indexable": len(chunks),
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


@router.put("/settings/embedding")
def put_embedding(body: EmbeddingBody) -> dict:
    save_embedding(api_key=body.apiKey, model=body.model, baseUrl=body.baseUrl)
    return {"ok": True, "settings": all_settings_json()}


@router.post("/settings/test-embedding")
async def test_embedding() -> dict:
    try:
        vector = await embedding.embed_text(EMBEDDING_TEST_TEXT, load_embedding())
        return {"ok": True, "dimension": len(vector), "message": f"连接成功 · 向量维度 {len(vector)}"}
    except Exception as exc:  # noqa: BLE001 —— 测试接口把失败原因原样返回给前端
        return {"ok": False, "message": str(exc)}
