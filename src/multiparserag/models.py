from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def now() -> str:
    return datetime.now(UTC).isoformat()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Asset(StrictModel):
    asset_id: str
    original_path: str
    object_key: str
    url: str
    media_type: str
    sha256: str
    size_bytes: int


class Document(StrictModel):
    schema_version: str = "1.0"
    document_id: str
    filename: str
    source_sha256: str
    source_media_type: str = "application/pdf"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    status: Literal["queued", "parsing", "publishing", "ready", "failed"] = "queued"
    parser: dict[str, str] = Field(default_factory=dict)
    markdown: str = ""
    markdown_url: str | None = None
    assets: list[Asset] = Field(default_factory=list)
    error: str | None = None
    index_status: Literal[
        "not_indexed", "uploading", "indexing", "indexed", "failed", "unknown"
    ] = "not_indexed"
    dataset_id: str | None = None
    ragflow_document_id: str | None = None
    index_error: str | None = None


class IndexRequest(StrictModel):
    dataset_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class ContextRequest(StrictModel):
    query: str = Field(min_length=1, max_length=4000)
    dataset_ids: list[str] = Field(default_factory=list, max_length=20)
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    top_k: int = Field(5, ge=1, le=20)
    similarity_threshold: float = Field(0.2, ge=0, le=1)
    max_context_chars: int = Field(12000, ge=500, le=40000)

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()


class Evidence(StrictModel):
    citation_id: str
    chunk_id: str
    document_id: str
    local_document_id: str | None = None
    dataset_id: str
    title: str
    content: str
    score: float
    positions: list[Any] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)


class ContextResponse(StrictModel):
    schema_version: str = "1.0"
    query: str
    context_text: str
    evidence: list[Evidence]
    assets: list[Asset]
    applied_filters: dict[str, Any]
    missing_information: list[str]
    truncated: bool = False
    provider: str = "ragflow"


class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 502, *, outcome_unknown: bool = False):
        self.message = message
        self.status_code = status_code
        self.outcome_unknown = outcome_unknown
        super().__init__(message)
