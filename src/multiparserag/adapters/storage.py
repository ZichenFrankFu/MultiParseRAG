"""Publish extracted raster images through the official MinIO SDK or local HTTP."""

import hashlib
import json
import shutil
from pathlib import Path
from urllib.parse import quote

import httpx
from minio import Minio
from PIL import Image
from urllib3 import PoolManager
from urllib3.util.retry import Retry
from urllib3.util.timeout import Timeout

from ..config import Settings
from ..models import Asset, ServiceError

FORMATS = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
}


def minio_client(settings: Settings) -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key.get_secret_value(),
        secret_key=settings.minio_secret_key.get_secret_value(),
        secure=settings.minio_secure,
        http_client=PoolManager(timeout=Timeout(connect=5, read=30), retries=Retry(total=2)),
    )


class AssetStorage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = minio_client(settings) if settings.storage_backend == "minio" else None

    def publish(self, document_id: str, path: Path, original_path: str) -> Asset:
        if path.stat().st_size > 25 * 1024 * 1024:
            raise ServiceError("单张图片超过 25 MiB。")
        try:
            with Image.open(path) as image:
                image.verify()
                extension, media_type = FORMATS[image.format]
        except (OSError, KeyError, Image.DecompressionBombError) as exc:
            raise ServiceError("解析结果中的图片格式无效，仅发布 PNG/JPEG/WebP/GIF。") from exc
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        key = f"{document_id}/{digest}.{extension}"
        if self.client:
            try:
                self.client.fput_object(
                    self.settings.minio_bucket,
                    key,
                    str(path),
                    content_type=media_type,
                )
                # Verify public-read policy anonymously using the server-reachable endpoint.
                scheme = "https" if self.settings.minio_secure else "http"
                check_url = (
                    f"{scheme}://{self.settings.minio_endpoint}/"
                    f"{self.settings.minio_bucket}/{quote(key, safe='/')}"
                )
                with httpx.Client(timeout=10, trust_env=False) as client:
                    response = client.head(check_url)
                    response.raise_for_status()
                base = self.settings.minio_public_base_url
            except Exception as exc:
                raise ServiceError(
                    "MinIO 图片上传或匿名读取失败，请先初始化图片桶并检查连接。"
                ) from exc
        else:
            target = self.settings.data_dir / "public" / key
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            base = f"{self.settings.public_base_url.rstrip('/')}/assets"
        return Asset(
            asset_id=digest,
            original_path=original_path,
            object_key=key,
            url=f"{base.rstrip('/')}/{quote(key, safe='/')}",
            media_type=media_type,
            sha256=digest,
            size_bytes=path.stat().st_size,
        )


def bootstrap():
    """Explicitly configure a dedicated image-only bucket for anonymous object reads."""
    settings = Settings()
    if settings.storage_backend == "local":
        (settings.data_dir / "public").mkdir(parents=True, exist_ok=True)
        print("Local image directory is ready.")
        return
    client = minio_client(settings)
    bucket = settings.minio_bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": ["*"]},
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/*"],
            }
        ],
    }
    client.set_bucket_policy(bucket, json.dumps(policy))
    print(f"Image bucket {bucket} is ready for anonymous object reads.")


if __name__ == "__main__":
    bootstrap()
