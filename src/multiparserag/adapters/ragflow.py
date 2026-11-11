"""RAGFlow v0.27.2 public API boundary; no local vector database reimplementation."""

from urllib.parse import quote

import httpx

from ..config import Settings
from ..models import ContextRequest, ServiceError


class RAGFlow:
    def __init__(self, settings: Settings):
        self.settings = settings

    def datasets(self, requested: list[str]) -> list[str]:
        if not self.settings.ragflow_api_key.get_secret_value() or not self.settings.dataset_ids:
            raise ServiceError("请先配置 RAGFlow API Key 与允许访问的知识库 ID。", 503)
        selected = requested or self.settings.dataset_ids
        if not set(selected).issubset(self.settings.dataset_ids):
            raise ServiceError("请求包含未授权的知识库。", 403)
        return selected

    def call(self, method: str, path: str, **kwargs):
        is_upload = method == "POST" and path.endswith("/documents") and "files" in kwargs
        try:
            with httpx.Client(
                base_url=self.settings.ragflow_base_url.rstrip("/") + "/",
                headers={
                    "Authorization": f"Bearer {self.settings.ragflow_api_key.get_secret_value()}"
                },
                timeout=60,
                follow_redirects=False,
            ) as client:
                response = client.request(method, path.lstrip("/"), **kwargs)
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload, dict) or payload.get("code") != 0:
                code = payload.get("code", "invalid") if isinstance(payload, dict) else "invalid"
                raise ServiceError(f"RAGFlow 返回业务错误（code={code}），请检查上游日志。")
            return payload.get("data")
        except (httpx.HTTPError, ValueError) as exc:
            ambiguous = is_upload and (
                not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code >= 500
            )
            raise ServiceError(
                "RAGFlow 请求失败，请检查地址、凭证和服务版本。",
                503,
                outcome_unknown=ambiguous,
            ) from exc

    def upload(self, dataset_id: str, name: str, markdown: str) -> str:
        self.datasets([dataset_id])
        data = self.call(
            "POST",
            f"api/v1/datasets/{quote(dataset_id, safe='')}/documents",
            files={"file": (name, markdown.encode(), "text/markdown")},
        )
        if (
            not isinstance(data, list)
            or not data
            or not isinstance(data[0], dict)
            or not data[0].get("id")
        ):
            raise ServiceError(
                "RAGFlow 上传响应缺少 document ID，请在上游确认上传状态。",
                outcome_unknown=True,
            )
        return data[0]["id"]

    def parse(self, dataset_id: str, document_id: str):
        self.datasets([dataset_id])
        self.call(
            "POST",
            f"api/v1/datasets/{quote(dataset_id, safe='')}/chunks",
            json={"document_ids": [document_id]},
        )

    def document(self, dataset_id: str, document_id: str):
        self.datasets([dataset_id])
        data = self.call(
            "GET",
            f"api/v1/datasets/{quote(dataset_id, safe='')}/documents",
            params={"id": document_id, "page_size": 1},
        )
        docs = data.get("docs", []) if isinstance(data, dict) else []
        if not docs or docs[0].get("id") != document_id:
            raise ServiceError("RAGFlow 中未找到对应文档。", 404)
        return docs[0]

    def retrieve(self, request: ContextRequest):
        datasets = self.datasets(request.dataset_ids)
        data = self.call(
            "POST",
            "api/v1/retrieval",
            json={
                "question": request.query,
                "dataset_ids": datasets,
                "document_ids": request.document_ids,
                "page": 1,
                "page_size": request.top_k,
                "similarity_threshold": request.similarity_threshold,
                "highlight": False,
            },
        )
        if not isinstance(data, dict) or not isinstance(data.get("chunks"), list):
            raise ServiceError("RAGFlow 检索响应缺少 chunks。")
        if any(not isinstance(chunk, dict) for chunk in data["chunks"]):
            raise ServiceError("RAGFlow 返回了无效的片段对象。")
        return datasets, data["chunks"]
