import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MPR_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(8080, ge=1, le=65535)
    data_dir: Path = Path("data")
    api_key: SecretStr = SecretStr("")
    public_base_url: str = "http://localhost:8080"
    max_upload_mb: int = Field(25, ge=1, le=200)
    mineru_mode: Literal["http", "cli"] = "http"
    mineru_base_url: str = "http://localhost:8003"
    mineru_api_key: SecretStr = SecretStr("")
    mineru_command: str = "mineru-kit"
    mineru_tier: Literal["flash", "basic", "standard", "advanced"] = "standard"
    parse_timeout_seconds: int = Field(900, ge=5, le=7200)
    storage_backend: Literal["minio", "local"] = "minio"
    minio_endpoint: str = "localhost:19000"
    minio_access_key: SecretStr = SecretStr("local-development")
    minio_secret_key: SecretStr = SecretStr("local-development-change-this")
    minio_secure: bool = False
    minio_bucket: str = "multiparserag-images"
    minio_public_base_url: str = "http://localhost:19000/multiparserag-images"
    milvus_uri: str = "http://localhost:19530"
    milvus_token: SecretStr = SecretStr("")
    milvus_collection: str = Field(
        "multiparserag_chunks", max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )
    milvus_dataset_ids: str = ""
    embedding_base_url: str = "http://localhost:11434/v1"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_model: str = ""
    embedding_dimension: int = Field(768, ge=2, le=32768)

    @property
    def dataset_ids(self) -> list[str]:
        return list(
            dict.fromkeys(x.strip() for x in self.milvus_dataset_ids.split(",") if x.strip())
        )

    @model_validator(mode="after")
    def validate_urls(self):
        for name in (
            "public_base_url",
            "mineru_base_url",
            "minio_public_base_url",
            "milvus_uri",
            "embedding_base_url",
        ):
            value = urlparse(getattr(self, name))
            if value.scheme not in {"http", "https"} or not value.netloc:
                raise ValueError(f"{name} must be an absolute HTTP(S) URL")
            if value.username or value.password or value.query or value.fragment:
                raise ValueError(f"{name} must not include credentials, query, or fragment")
        if "://" in self.minio_endpoint:
            raise ValueError("minio_endpoint must be host:port, without a URL scheme")
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", dataset) for dataset in self.dataset_ids):
            raise ValueError("MPR_MILVUS_DATASET_IDS contains an invalid dataset ID")
        self.embedding_model = self.embedding_model.strip()
        if (
            self.host not in {"localhost", "127.0.0.1", "::1"}
            and not self.api_key.get_secret_value()
        ):
            raise ValueError("MPR_API_KEY is required when binding beyond localhost")
        return self
