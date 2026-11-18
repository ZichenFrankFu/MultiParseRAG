import json
import zipfile
from unittest.mock import Mock

import httpx
import pytest
from PIL import Image

from multiparserag.adapters.embeddings import Embeddings
from multiparserag.adapters.milvus import CHUNK_LENGTH, Milvus, split_markdown
from multiparserag.adapters.mineru import MinerU, unpack_result
from multiparserag.adapters.storage import AssetStorage
from multiparserag.assets import publish_images
from multiparserag.models import Document, ServiceError


def use_transport(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(handler))
    )


def test_mineru_http_polling_contract(settings, mineru_zip, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            assert request.url.path == "/prefix/v1/file_parse"
            body = request.read()
            for value in [
                b'name="file"',
                b'name="request_id"',
                b'name="output_formats"',
                b"default",
            ]:
                assert value in body
            return httpx.Response(202, json={"status": "running"})
        if request.url.path.endswith("/result"):
            return httpx.Response(200, content=mineru_zip)
        return httpx.Response(200, json={"status": "completed", "result_url": "http://untrusted/"})

    settings.mineru_base_url = "http://parser/prefix"
    use_transport(monkeypatch, handler)
    source = settings.data_dir / "source.pdf"
    source.write_bytes(b"%PDF-x")
    markdown = MinerU(settings).parse(source, settings.data_dir, "request1")
    assert markdown.name == "markdown.md"
    assert len(calls) == 3
    assert calls[-1].url.host == "parser"
    assert calls[-1].url.path == "/prefix/v1/file_parse/request1/result"


def test_mineru_rejects_json_success(settings, monkeypatch):
    use_transport(monkeypatch, lambda req: httpx.Response(200, json={"wrong": "protocol"}))
    source = settings.data_dir / "source.pdf"
    source.write_bytes(b"%PDF-x")
    with pytest.raises(ServiceError):
        MinerU(settings).parse(source, settings.data_dir, "req")
    assert not (settings.data_dir / "mineru.zip").exists()


@pytest.mark.parametrize(
    "entry", ["../escape.md", "/absolute.md", "C:/absolute.md", "..\\escape.md"]
)
def test_zip_path_traversal(tmp_path, entry):
    archive = tmp_path / "result.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(entry, "bad")
    with pytest.raises(ServiceError):
        unpack_result(archive, tmp_path / "raw")
    assert not (tmp_path / "escape.md").exists()


def test_symlink_and_zip_expansion_limit(tmp_path):
    archive = tmp_path / "result.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        entry = zipfile.ZipInfo("link")
        entry.external_attr = 0o120777 << 16
        bundle.writestr(entry, "../../secret")
    with pytest.raises(ServiceError):
        unpack_result(archive, tmp_path / "raw")


def test_all_mineru_image_styles(settings):
    raw = settings.data_dir / "raw"
    (raw / "images").mkdir(parents=True)
    Image.new("RGB", (8, 8), "blue").save(raw / "images" / "a.png")
    md = raw / "markdown.md"
    md.write_text(
        '![one](images/a.png)\n\n![two][pic]\n\n[pic]: images/a.png "title"\n\n'
        '<table><tr><td><img src="images/a.png"></td></tr></table>',
        encoding="utf-8",
    )
    result, assets = publish_images(md, raw, "doc", AssetStorage(settings))
    assert len(assets) == 1
    assert result.count(assets[0].url) == 3
    assert "images/a.png" not in result
    assert '"title"' in result


@pytest.mark.parametrize(
    "source", ["../private.png", "images/missing.png", "https://outside/x.png"]
)
def test_missing_or_external_images_fail(settings, source):
    raw = settings.data_dir / "raw"
    raw.mkdir()
    md = raw / "markdown.md"
    md.write_text(f"![x]({source})")
    with pytest.raises(ServiceError):
        publish_images(md, raw, "doc", AssetStorage(settings))


def test_minio_upload_public_verification_and_url(settings, monkeypatch):
    settings.storage_backend = "minio"
    settings.minio_public_base_url = "https://assets.example.com/multiparserag-images"
    storage = AssetStorage(settings)
    storage.client = Mock()
    requests = []

    def handler(request):
        requests.append(request)
        assert "authorization" not in request.headers
        return httpx.Response(200)

    use_transport(monkeypatch, handler)
    png = settings.data_dir / "chart.png"
    Image.new("RGB", (8, 8), "blue").save(png)
    asset = storage.publish("doc-id", png, "images/chart.png")
    assert asset.url.startswith("https://assets.example.com/multiparserag-images/doc-id/")
    assert storage.client.fput_object.call_args.kwargs["content_type"] == "image/png"
    assert requests[0].method == "HEAD"


def test_private_minio_bucket_fails(settings, monkeypatch):
    settings.storage_backend = "minio"
    storage = AssetStorage(settings)
    storage.client = Mock()
    use_transport(monkeypatch, lambda req: httpx.Response(403))
    png = settings.data_dir / "chart.png"
    Image.new("RGB", (8, 8), "blue").save(png)
    with pytest.raises(ServiceError, match="匿名读取"):
        storage.publish("doc", png, "images/chart.png")


def test_embeddings_contract_orders_vectors_by_index(settings, monkeypatch):
    settings.embedding_base_url = "http://embeddings.test/v1"

    def handler(request):
        assert request.headers["authorization"] == "Bearer test-key"
        assert request.url.path == "/v1/embeddings"
        assert json.loads(request.content) == {
            "model": "test-embedding",
            "input": ["检索词", "document"],
        }
        return httpx.Response(
            200,
            json={
                "data": [{"index": 1, "embedding": [0, 1, 0]}, {"index": 0, "embedding": [1, 0, 0]}]
            },
        )

    use_transport(monkeypatch, handler)
    assert Embeddings(settings).embed(["检索词", "document"]) == [[1, 0, 0], [0, 1, 0]]


def test_embeddings_reject_invalid_dimension(settings, monkeypatch):
    use_transport(
        monkeypatch,
        lambda req: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0]}]}),
    )
    with pytest.raises(ServiceError, match="维度"):
        Embeddings(settings).embed(["fact"])


def test_milvus_retry_reuses_chunk_ids_and_preserves_content(settings, monkeypatch):
    doc = Document(
        document_id="doc-id",
        filename="source.pdf",
        source_sha256="abc",
        markdown="# 标题\n\n" + "字" * (CHUNK_LENGTH + 1) + "\n\n尾段。",
    )
    chunks = split_markdown(doc.markdown)
    assert all(0 < len(chunk) <= CHUNK_LENGTH for chunk in chunks)
    assert "".join("".join(chunks).split()) == "".join(doc.markdown.split())

    adapter = Milvus(settings)
    monkeypatch.setattr(adapter, "_collection", Mock(return_value=True))
    monkeypatch.setattr(adapter.embeddings, "embed", lambda texts: [[1, 0, 0] for _ in texts])
    store = Mock()
    store.upsert.side_effect = [{"upsert_count": 0}, {"upsert_count": len(chunks)}]
    monkeypatch.setattr(adapter, "_client", store)
    with pytest.raises(ServiceError, match="未确认"):
        adapter.index(doc, "kb-1")
    adapter.index(doc, "kb-1")

    first, second = (call.args[1] for call in store.upsert.call_args_list)
    assert first == second
    assert len({row["id"] for row in second}) == len(chunks)
    assert [row["content"] for row in second] == chunks
    assert {row["dataset_id"] for row in second} == {"kb-1"}
