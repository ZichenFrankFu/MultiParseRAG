import math

import httpx

from ..config import Settings
from ..models import ServiceError


class Embeddings:
    """OpenAI-compatible text embeddings for both indexing and search."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def model(self) -> str:
        if not self.settings.embedding_model:
            raise ServiceError("请先配置嵌入模型 MPR_EMBEDDING_MODEL。", 503)
        return self.settings.embedding_model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self.model
        key = self.settings.embedding_api_key.get_secret_value()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            with httpx.Client(
                base_url=self.settings.embedding_base_url.rstrip("/") + "/",
                headers=headers,
                timeout=60,
                follow_redirects=False,
            ) as client:
                response = client.post("embeddings", json={"model": model, "input": texts})
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceError("嵌入请求失败，请检查服务地址、模型和凭证。", 503) from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        vectors = {}
        if not isinstance(data, list) or len(data) != len(texts):
            raise ServiceError("嵌入响应的向量数量与输入不一致。")
        for item in data:
            index = item.get("index") if isinstance(item, dict) else None
            if type(index) is not int or not 0 <= index < len(texts) or index in vectors:
                raise ServiceError("嵌入响应包含无效或重复的输入序号。")
            vector = item.get("embedding")
            if not isinstance(vector, list) or len(vector) != self.settings.embedding_dimension:
                raise ServiceError("嵌入向量维度与 MPR_EMBEDDING_DIMENSION 不一致。")
            if any(type(value) not in {int, float} for value in vector):
                raise ServiceError("嵌入响应包含无效的向量。")
            try:
                values = [float(value) for value in vector]
            except OverflowError as exc:
                raise ServiceError("嵌入响应包含无效的向量。") from exc
            if not any(values) or not all(
                math.isfinite(value) and abs(value) <= 3.4028234663852886e38 for value in values
            ):
                raise ServiceError("嵌入响应包含无效的向量。")
            vectors[index] = values
        return [vectors[index] for index in range(len(texts))]
