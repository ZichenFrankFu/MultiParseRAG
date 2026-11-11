import logging
import math
from pathlib import Path
from threading import Lock

from .adapters.mineru import MinerU
from .adapters.ragflow import RAGFlow
from .adapters.storage import AssetStorage
from .assets import publish_images
from .config import Settings
from .models import ContextRequest, ContextResponse, Evidence, ServiceError
from .repository import Repository

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repository = repository
        self.parser = MinerU(settings)
        self.storage = AssetStorage(settings)
        self.ragflow = RAGFlow(settings)
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
        self.ragflow.datasets([dataset_id])
        if not self.index_lock.acquire(blocking=False):
            raise ServiceError("已有索引提交正在执行，请稍后重试。", 409)
        try:
            doc = self.repository.get(document_id)
            if not doc or doc.status != "ready":
                raise ServiceError("文档需完成解析后才能提交索引。", 409)
            if doc.dataset_id and doc.dataset_id != dataset_id:
                raise ServiceError("v0.0.1 每份文档仅关联一个知识库。", 409)
            if doc.index_status in {"uploading", "unknown"}:
                raise ServiceError(
                    "上传结果尚未确认，请先核对并关联 RAGFlow 文档，避免重复上传。", 409
                )
            if doc.index_status in {"indexing", "indexed"}:
                return doc
            if not doc.ragflow_document_id:
                self.repository.update(document_id, index_status="uploading", dataset_id=dataset_id)
                try:
                    remote_id = self.ragflow.upload(dataset_id, f"{document_id}.md", doc.markdown)
                except ServiceError as exc:
                    self.repository.update(
                        document_id,
                        index_status="unknown" if exc.outcome_unknown else "failed",
                        index_error=(
                            "上传结果未确认，请在 RAGFlow 核对文档 ID。"
                            if exc.outcome_unknown
                            else exc.message
                        ),
                    )
                    raise
                doc = self.repository.update(document_id, ragflow_document_id=remote_id)
            try:
                self.ragflow.parse(dataset_id, doc.ragflow_document_id)
            except ServiceError as exc:
                self.repository.update(document_id, index_status="failed", index_error=exc.message)
                raise
            return self.repository.update(document_id, index_status="indexing", index_error=None)
        finally:
            self.index_lock.release()

    def refresh_index(self, document_id: str):
        doc = self.repository.get(document_id)
        if not doc.ragflow_document_id:
            raise ServiceError("文档尚未关联 RAGFlow 索引。", 409)
        remote = self.ragflow.document(doc.dataset_id, doc.ragflow_document_id)
        state = str(remote.get("run", "")).upper()
        status = (
            "indexed"
            if state in {"3", "DONE"}
            else "failed"
            if state in {"2", "4", "CANCEL", "FAIL"}
            else "indexing"
        )
        return self.repository.update(
            document_id,
            index_status=status,
            index_error="RAGFlow 索引失败或已取消，请检查上游任务。"
            if status == "failed"
            else None,
        )

    def context(self, request: ContextRequest) -> ContextResponse:
        datasets, chunks = self.ragflow.retrieve(request)
        lookup = {d.ragflow_document_id: d for d in self.repository.list() if d.ragflow_document_id}
        evidence, assets, blocks = [], {}, []
        length, truncated = 0, False
        seen = set()
        for chunk in chunks:
            if len(evidence) >= request.top_k:
                break
            dataset = chunk.get("dataset_id")
            document = chunk.get("document_id")
            chunk_id = chunk.get("id")
            if dataset not in datasets or (
                request.document_ids and document not in request.document_ids
            ):
                continue
            if not chunk_id or chunk_id in seen or not document:
                continue
            seen.add(chunk_id)
            content = str(chunk.get("content", "")).strip()
            try:
                score = float(chunk.get("similarity", 0))
            except (TypeError, ValueError) as exc:
                raise ServiceError("RAGFlow 返回了无效的相似度。") from exc
            if not math.isfinite(score):
                raise ServiceError("RAGFlow 返回了无效的相似度。")
            if not content or score < request.similarity_threshold:
                continue
            citation = f"E{len(evidence) + 1}"
            title = str(chunk.get("document_keyword") or document)
            prefix = f"[{citation}] {title}\n"
            budget = request.max_context_chars - length - len(prefix) - (2 if blocks else 0)
            if budget <= 0:
                truncated = True
                break
            if len(content) > budget:
                content = content[:budget]
                truncated = True
            local = lookup.get(document)
            matched = [a for a in local.assets if a.url in content] if local else []
            assets.update({a.asset_id: a for a in matched})
            evidence.append(
                Evidence(
                    citation_id=citation,
                    chunk_id=chunk_id,
                    document_id=document,
                    local_document_id=local.document_id if local else None,
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
        missing = [] if evidence else ["未检索到满足条件的证据；请补充资料或调整查询。"]
        if truncated:
            missing.append("上下文已按字符预算截断，可扩大预算或缩小检索范围。")
        return ContextResponse(
            query=request.query,
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
