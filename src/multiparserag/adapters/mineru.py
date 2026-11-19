"""MinerU 4.1.7 CLI / file-parse-server integration. No models in the API process."""

import shutil
import stat
import subprocess
import time
import zipfile
from pathlib import Path, PurePosixPath

import httpx

from ..config import Settings
from ..models import ServiceError

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024


def unpack_result(archive: Path, output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if len(entries) > 5000 or sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES:
                raise ServiceError("MinerU 输出超过解压限制。")
            for item in entries:
                path = PurePosixPath(item.filename)
                mode = item.external_attr >> 16
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in item.filename
                    or ":" in item.filename
                    or stat.S_ISLNK(mode)
                ):
                    raise ServiceError("MinerU 输出包含不安全的文件路径。")
                target = output.joinpath(*path.parts)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(item) as source, target.open("wb") as dest:
                        shutil.copyfileobj(source, dest)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise ServiceError("MinerU 未返回有效的结果 ZIP。") from exc
    markdown = list(output.rglob("*.md"))
    if len(markdown) != 1:
        raise ServiceError("单文档结果包必须包含一份 Markdown。")
    return markdown[0]


class MinerU:
    def __init__(self, settings: Settings):
        self.settings = settings

    def parse(self, source: Path, work_dir: Path, request_id: str) -> Path:
        archive = work_dir / "mineru.zip"
        if not archive.exists():
            if self.settings.mineru_mode == "cli":
                self._cli(source, archive)
            else:
                self._http(source, archive, request_id)
        return unpack_result(archive, work_dir / "raw")

    def _cli(self, source: Path, archive: Path):
        binary = shutil.which(self.settings.mineru_command)
        if not binary:
            raise ServiceError("找不到 mineru-kit。请安装 MinerU 4.1.7 或配置远程解析服务。", 503)
        temporary = archive.with_name("result.pending.zip")
        try:
            # Argument list avoids shell interpolation; filename is a server-generated source.pdf.
            subprocess.run(
                [
                    binary,
                    "parse",
                    str(source.resolve()),
                    "-o",
                    str(temporary.resolve()),
                    "--format",
                    "zip",
                    "--tier",
                    self.settings.mineru_tier,
                    "--ocr-mode",
                    "auto",
                ],
                check=True,
                timeout=self.settings.parse_timeout_seconds,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise ServiceError("MinerU 解析超时，请缩小文档或提高超时设置。", 504) from exc
        except subprocess.CalledProcessError as exc:
            raise ServiceError("MinerU 命令执行失败，请检查推理环境与模型安装。") from exc
        if not temporary.is_file() or temporary.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ServiceError("MinerU 输出缺失或超过 128 MiB。")
        temporary.replace(archive)

    def _http(self, source: Path, archive: Path, request_id: str):
        base = self.settings.mineru_base_url.rstrip("/")
        route = f"{base}/v1/file_parse"
        headers = {}
        key = self.settings.mineru_api_key.get_secret_value()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        # Own request ID and fixed same-origin paths: never follow untrusted result URLs.
        deadline = time.monotonic() + self.settings.parse_timeout_seconds
        try:
            with httpx.Client(headers=headers, timeout=60, follow_redirects=False) as client:
                with (
                    source.open("rb") as handle,
                    client.stream(
                        "POST",
                        route,
                        files={"file": ("source.pdf", handle, "application/pdf")},
                        data={
                            "request_id": request_id,
                            "output_formats": "default",
                            "tier": self.settings.mineru_tier,
                            "wait_timeout": "0",
                        },
                    ) as response,
                ):
                    if response.status_code == 200:
                        self._save_download(response, archive)
                        return
                    if response.status_code != 202:
                        raise ServiceError(f"MinerU 提交失败（HTTP {response.status_code}）。")
                while time.monotonic() < deadline:
                    response = client.get(f"{route}/{request_id}")
                    response.raise_for_status()
                    status = response.json().get("status")
                    if status == "completed":
                        with client.stream("GET", f"{route}/{request_id}/result") as result:
                            result.raise_for_status()
                            self._save_download(result, archive)
                        return
                    if status in {"failed", "canceled", "submission_unknown"}:
                        raise ServiceError(f"MinerU 任务状态：{status}。请求 ID：{request_id}")
                    time.sleep(2)
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceError(
                "MinerU 服务请求失败；请检查连接，重试将沿用原请求 ID。", 503
            ) from exc
        raise ServiceError("MinerU 等待超时；可重试同一任务获取已有结果。", 504)

    @staticmethod
    def _save_download(response: httpx.Response, archive: Path):
        temporary = archive.with_suffix(".part")
        total = 0
        try:
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ServiceError("MinerU 结果包超过 128 MiB。")
                    handle.write(chunk)
            if not zipfile.is_zipfile(temporary):
                raise ServiceError("MinerU 响应并非 ZIP 结果包，请核对服务版本和地址。")
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
