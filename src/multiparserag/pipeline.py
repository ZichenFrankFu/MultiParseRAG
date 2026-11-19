import logging
import math
from pathlib import Path
from threading import Lock

from .adapters.milvus import Milvus
from .adapters.mineru import MinerU
from .adapters.storage import AssetStorage
from .assets import publish_images
from .config import Settings
from .intent import resolve_intent
from .models import ContextRequest, ContextResponse, Evidence, ServiceError
from .repository import Repository

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repository = repository
        self.parser = MinerU(settings)
        self.storage = AssetStorage(settings)
        self.vectors = Milvus(settings)
        self.index_lock = Lock()

    def directory(self, document_id: str) -> Path:
        return self.settings.data_dir / "documents" / document_id

    def convert(self, document_id: str):
        try:
            logger.info("parse_started document_id=%s", document_id)
            self.repository.update(document_id, status="parsing", error=None)
            work = self.directory(document_id)
            markdown_path = self.parser.parse(work / "source.pdf", work, document_id)
            self.repository.update(document_id, status="publishing")
            markdown, assets = publish_images(
                markdown_path,
                work / "raw",
                document_id,
                self.storage,
            )
            (work / "document.md").write_text(markdown, encoding="utf-8")
            doc = self.repository.update(
                document_id,
                status="ready",
                markdown=markdown,
                assets=assets,
                markdown_url=f"/api/v1/documents/{document_id}/markdown",
                parser={
                    "name": "MinerU",
                    "target_version": "4.1.7",
                    "mode": self.settings.mineru_mode,
                    "tier": self.settings.mineru_tier,
                },
            )
            (work / "manifest.json").write_text(doc.model_dump_json(indent=2), encoding="utf-8")
            logger.info("parse_completed document_id=%s assets=%s", document_id, len(assets))
        except Exception as exc:
            message = (
                exc.message
                if isinstance(exc, ServiceError)
                else "处理失败，请检查服务日志和存储空间。"
            )
            # Avoid logging source contents, credentials, or upstream response bodies.
            logger.error(
                "parse_failed document_id=%s error_type=%s", document_id, type(exc).__name__
            )
            self.repository.update(document_id, status="failed", error=message)

    def index(self, document_id: str, dataset_id: str):
        self.vectors.datasets([dataset_id])
        if not self.index_lock.acquire(blocking=False):
            raise ServiceError("已有索引提交正在执行，请稍后重试。", 409)
        try:
            doc = self.repository.get(document_id)
            if not doc or doc.status != "ready":
                raise ServiceError("文档需完成解析后才能提交索引。", 409)
            if doc.index_provider == "milvus" and doc.dataset_id != dataset_id:
                raise ServiceError("每份文档仅关联一个 Milvus 知识库。", 409)
            if (
                doc.index_provider == "milvus"
                and doc.index_status == "indexed"
                and self.vectors.is_indexed(doc)
            ):
                return doc
            self.repository.update(
                document_id,
                index_provider="milvus",
                index_status="indexing",
                dataset_id=dataset_id,
                index_error=None,
            )
            try:
                self.vectors.index(doc, dataset_id)
            except Exception as exc:
                message = exc.message if isinstance(exc, ServiceError) else "索引失败，请检查日志。"
                self.repository.update(document_id, index_status="failed", index_error=message)
                raise
            return self.repository.update(document_id, index_status="indexed", index_error=None)
        finally:
            self.index_lock.release()

    def refresh_index(self, document_id: str):
        if not self.index_lock.acquire(blocking=False):
            raise ServiceError("已有索引提交正在执行，请稍后重试。", 409)
        try:
            doc = self.repository.get(document_id)
            if not doc or doc.index_provider != "milvus" or not doc.dataset_id:
                raise ServiceError("文档尚未提交 Milvus 索引。", 409)
            self.vectors.datasets([doc.dataset_id])
            indexed = self.vectors.is_indexed(doc)
            return self.repository.update(
                document_id,
                index_status="indexed" if indexed else "failed",
                index_error=None if indexed else "Milvus 索引缺失或不完整，请重新提交索引。",
            )
        finally:
            self.index_lock.release()

    def retrieve(self, request: ContextRequest) -> tuple[list[str], list[dict]]:
        datasets = self.vectors.datasets(request.dataset_ids)
        requested_ids = set(request.document_ids)
        documents = [
            doc.document_id
            for doc in self.repository.list()
            if doc.status == "ready"
            and doc.index_provider == "milvus"
            and doc.index_status == "indexed"
            and doc.dataset_id in datasets
            and (
                not requested_ids
                or doc.document_id in requested_ids
                or doc.ragflow_document_id in requested_ids
            )
        ]
        return datasets, self.vectors.retrieve(request, datasets, documents)

    def context(self, request: ContextRequest) -> ContextResponse:
        intent, retrieval_query = resolve_intent(request)
        if retrieval_query is None:
            datasets = self.vectors.datasets(request.dataset_ids) if request.dataset_ids else []
            chunks = []
        else:
            search = request.model_copy(update={"query": retrieval_query})
            datasets, chunks = self.retrieve(search)
        lookup = {doc.document_id: doc for doc in self.repository.list()} if chunks else {}
        evidence, assets, blocks = [], {}, []
        length, truncated = 0, False
        seen = set()
        for chunk in chunks:
            if len(evidence) >= request.top_k:
                break
            dataset = chunk.get("dataset_id")
            document = chunk.get("document_id")
            chunk_id = chunk.get("id")
            local = lookup.get(document)
            if (
                dataset not in datasets
                or not local
                or local.dataset_id != dataset
                or local.index_provider != "milvus"
                or local.index_status != "indexed"
                or (
                    request.document_ids
                    and document not in request.document_ids
                    and local.ragflow_document_id not in request.document_ids
                )
                or not chunk_id
                or chunk_id in seen
            ):
                continue
            seen.add(chunk_id)
            content = str(chunk.get("content", "")).strip()
            try:
                score = float(chunk.get("similarity", 0))
            except (TypeError, ValueError) as exc:
                raise ServiceError("Milvus 返回了无效的相似度。") from exc
            if not math.isfinite(score):
                raise ServiceError("Milvus 返回了无效的相似度。")
            if not content or score < request.similarity_threshold:
                continue
            citation = f"E{len(evidence) + 1}"
            title = str(chunk.get("title") or local.filename)
            prefix = f"[{citation}] {title}\n"
            budget = request.max_context_chars - length - len(prefix) - (2 if blocks else 0)
            if budget <= 0:
                truncated = True
                break
            if len(content) > budget:
                content = content[:budget]
                truncated = True
            matched = [a for a in local.assets if a.url in content]
            assets.update({a.asset_id: a for a in matched})
            evidence.append(
                Evidence(
                    citation_id=citation,
                    chunk_id=chunk_id,
                    document_id=document,
                    local_document_id=local.document_id,
                    dataset_id=dataset,
                    title=title,
                    content=content,
                    score=score,
                    positions=chunk.get("positions") or [],
                    asset_ids=[a.asset_id for a in matched],
                )
            )
            block = prefix + content
            length += len(block) + (2 if blocks else 0)
            blocks.append(block)
        missing = []
        if not evidence and retrieval_query is not None:
            missing.append("未检索到满足条件的证据，请补充资料或调整查询。")
        if truncated:
            missing.append("上下文已按字符预算截断，可扩大预算或缩小检索范围。")
        return ContextResponse(
            query=request.query,
            intent=intent,
            retrieval_query=retrieval_query,
            context_text="\n\n".join(blocks),
            evidence=evidence,
            assets=list(assets.values()),
            applied_filters={
                "dataset_ids": datasets,
                "document_ids": request.document_ids,
                "similarity_threshold": request.similarity_threshold,
            },
            missing_information=missing,
            truncated=truncated,
        )
