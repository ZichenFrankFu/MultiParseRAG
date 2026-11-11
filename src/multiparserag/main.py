import hashlib
import json
import logging
import secrets
import shutil
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from . import __version__
from .config import Settings
from .models import (
    ContextRequest,
    ContextResponse,
    Document,
    IndexRequest,
    ServiceError,
    StrictModel,
)
from .pipeline import Pipeline
from .repository import Repository


class LinkIndexRequest(IndexRequest):
    document_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class DocumentSummary(StrictModel):
    items: list[Document]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    repository = Repository(settings.data_dir)
    pipeline = Pipeline(settings, repository)
    public = settings.data_dir / "public"
    public.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repository.recover()
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="parse") as executor:
            app.state.executor = executor
            for document in repository.list():
                if document.status == "queued":
                    executor.submit(pipeline.convert, document.document_id)
            yield

    app = FastAPI(title="MultiParseRAG", version=__version__, lifespan=lifespan)
    app.state.pipeline = pipeline
    app.state.repository = repository
    app.state.settings = settings

    @app.exception_handler(ServiceError)
    async def service_error(_request, exc: ServiceError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    def authorize(request: Request):
        expected = settings.api_key.get_secret_value()
        provided = request.headers.get("X-API-Key", "")
        if expected and not secrets.compare_digest(provided.encode(), expected.encode()):
            raise HTTPException(401, "需要有效的 X-API-Key。")

    auth = [Depends(authorize)]

    def get_document(document_id: UUID) -> Document:
        doc = repository.get(str(document_id))
        if doc is None:
            raise HTTPException(404, "文档不存在。")
        return doc

    @app.get("/healthz")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/status", dependencies=auth)
    def status():
        return {
            "version": __version__,
            "storage_backend": settings.storage_backend,
            "mineru_mode": settings.mineru_mode,
            "mineru_target_version": "4.1.7",
            "mineru_configured": settings.mineru_mode == "http"
            or bool(shutil.which(settings.mineru_command)),
            "ragflow_configured": bool(
                settings.ragflow_api_key.get_secret_value() and settings.dataset_ids
            ),
            "dataset_ids": settings.dataset_ids,
            "max_upload_mb": settings.max_upload_mb,
            "connectivity_verified": False,
        }

    @app.post("/api/v1/documents", status_code=202, response_model=Document, dependencies=auth)
    async def ingest(
        request: Request,
        file: Annotated[UploadFile, File()],
        metadata: Annotated[str, Form()] = "{}",
    ):
        try:
            attributes = json.loads(metadata)
            if not isinstance(attributes, dict) or len(metadata.encode("utf-8")) > 16384:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(422, "metadata 必须为不超过 16 KiB 的 JSON 对象。") from exc
        name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not name.lower().endswith(".pdf") or len(name) > 255:
            raise HTTPException(415, "v0.0.1 接收 PDF 文件。")
        document_id = str(uuid4())
        work = pipeline.directory(document_id)
        work.mkdir(parents=True)
        digest = hashlib.sha256()
        size = 0
        try:
            with (work / "source.pdf").open("wb") as handle:
                first = True
                while chunk := await file.read(1024 * 1024):
                    if first and not chunk.startswith(b"%PDF-"):
                        raise HTTPException(415, "文件内容缺少 PDF 签名。")
                    first = False
                    size += len(chunk)
                    if size > settings.max_upload_mb * 1024 * 1024:
                        raise HTTPException(413, "文件超过上传大小限制。")
                    digest.update(chunk)
                    handle.write(chunk)
            if size == 0:
                raise HTTPException(415, "PDF 文件为空。")
            document = Document(
                document_id=document_id,
                filename=name,
                source_sha256=digest.hexdigest(),
                metadata=attributes,
            )
            repository.save(document)
        except Exception:
            shutil.rmtree(work, ignore_errors=True)
            raise
        finally:
            await file.close()
        request.app.state.executor.submit(pipeline.convert, document_id)
        return document

    @app.get("/api/v1/documents", response_model=DocumentSummary, dependencies=auth)
    def list_documents():
        # Small-volume starter catalog. Pagination is planned before larger deployments.
        return {"items": repository.list()}

    @app.get("/api/v1/documents/{document_id}", response_model=Document, dependencies=auth)
    def read_document(document_id: UUID):
        return get_document(document_id)

    @app.get("/api/v1/documents/{document_id}/markdown", dependencies=auth)
    def download_markdown(document_id: UUID):
        doc = get_document(document_id)
        if doc.status != "ready":
            raise HTTPException(409, "Markdown 尚未生成。")
        return FileResponse(
            pipeline.directory(str(document_id)) / "document.md",
            media_type="text/markdown",
            filename=f"{Path(doc.filename).stem}.md",
        )

    @app.post("/api/v1/documents/{document_id}/retry", response_model=Document, dependencies=auth)
    def retry(document_id: UUID, request: Request):
        with repository.lock:
            doc = get_document(document_id)
            if doc.status != "failed":
                raise HTTPException(409, "仅失败任务可以重试。")
            doc = repository.update(str(document_id), status="queued", error=None)
        request.app.state.executor.submit(pipeline.convert, str(document_id))
        return doc

    @app.post("/api/v1/documents/{document_id}/index", response_model=Document, dependencies=auth)
    def index(document_id: UUID, body: IndexRequest):
        get_document(document_id)
        return pipeline.index(str(document_id), body.dataset_id)

    @app.post(
        "/api/v1/documents/{document_id}/index/refresh", response_model=Document, dependencies=auth
    )
    def refresh_index(document_id: UUID):
        get_document(document_id)
        return pipeline.refresh_index(str(document_id))

    @app.post(
        "/api/v1/documents/{document_id}/index/link", response_model=Document, dependencies=auth
    )
    def link_index(document_id: UUID, body: LinkIndexRequest):
        doc = get_document(document_id)
        if doc.status != "ready" or doc.index_status != "unknown":
            raise HTTPException(409, "仅允许为上传结果未确认的已解析文档恢复关联。")
        if doc.dataset_id != body.dataset_id:
            raise HTTPException(409, "知识库与原提交不一致。")
        remote = pipeline.ragflow.document(body.dataset_id, body.document_id)
        if remote.get("name") != f"{document_id}.md":
            raise HTTPException(409, "远程文档名称与本次提交不一致。")
        return repository.update(
            str(document_id),
            dataset_id=body.dataset_id,
            ragflow_document_id=body.document_id,
            index_status="failed",
            index_error="关联已恢复，可再次提交解析。",
        )

    @app.post("/api/v1/context", response_model=ContextResponse, dependencies=auth)
    def context(body: ContextRequest):
        return pipeline.context(body)

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")
    app.mount("/assets", StaticFiles(directory=public, follow_symlink=False), name="assets")

    @app.get("/", include_in_schema=False)
    def index_page():
        return FileResponse(static / "index.html")

    return app


def run():
    settings = Settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, workers=1)


if __name__ == "__main__":
    run()
