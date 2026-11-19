import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from multiparserag.adapters.mineru import unpack_result
from multiparserag.main import create_app
from multiparserag.models import Document, ServiceError
from multiparserag.repository import Repository


def wait_document(client, document_id):
    for _ in range(100):
        doc = client.get(f"/api/v1/documents/{document_id}").json()
        if doc["status"] in {"ready", "failed"}:
            return doc
        time.sleep(0.02)
    pytest.fail("worker did not complete")


def test_pdf_to_markdown_and_public_image(settings, mineru_zip):
    app = create_app(settings)

    def parse(source, work, request_id):
        assert source.read_bytes().startswith(b"%PDF-")
        archive = work / "mineru.zip"
        archive.write_bytes(mineru_zip)
        return unpack_result(archive, work / "raw")

    app.state.pipeline.parser.parse = parse
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        response = client.post(
            "/api/v1/documents",
            files={"file": ("中文.PDF", b"%PDF-test")},
            data={"metadata": '{"project":"arbitrary-business"}'},
        )
        assert response.status_code == 202
        doc = wait_document(client, response.json()["document_id"])
        assert doc["status"] == "ready"
        assert doc["metadata"]["project"] == "arbitrary-business"
        assert len(doc["assets"]) == 1
        image_url = doc["assets"][0]["url"]
        assert image_url in doc["markdown"]
        image = client.get(image_url)
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        assert client.get(doc["markdown_url"]).text == doc["markdown"]
        assert (
            client.get("/data/documents/" + doc["document_id"] + "/source.pdf").status_code == 404
        )
        assert client.get("/assets/../catalog.sqlite3").status_code == 404
        assert client.post(f"/api/v1/documents/{doc['document_id']}/retry").status_code == 409
    assert Repository(settings.data_dir).get(doc["document_id"]).status == "ready"
    assert (settings.data_dir / "documents" / doc["document_id"] / "manifest.json").exists()


@pytest.mark.parametrize(
    "name,body,metadata,status",
    [
        ("a.txt", b"%PDF-x", "{}", 415),
        ("a.pdf", b"bad", "{}", 415),
        ("a.pdf", b"", "{}", 415),
        ("a.pdf", b"%PDF-x", "[]", 422),
        ("a.pdf", b"%PDF-x", "bad-json", 422),
        ("a.pdf", b"%PDF-" + b"x" * 1024 * 1024, "{}", 413),
    ],
)
def test_reject_invalid_uploads(settings, name, body, metadata, status):
    settings.max_upload_mb = 1
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/documents", files={"file": (name, body)}, data={"metadata": metadata}
        )
        assert response.status_code == status
        assert client.get("/api/v1/documents").json()["items"] == []


def test_failure_retry(settings, mineru_zip):
    app = create_app(settings)
    calls = []

    def parse(source, work, request_id):
        calls.append(request_id)
        if len(calls) == 1:
            raise ServiceError("simulated outage")
        archive = work / "mineru.zip"
        archive.write_bytes(mineru_zip)
        return unpack_result(archive, work / "raw")

    app.state.pipeline.parser.parse = parse
    with TestClient(app) as client:
        response = client.post("/api/v1/documents", files={"file": ("a.pdf", b"%PDF-test")})
        document_id = response.json()["document_id"]
        assert wait_document(client, document_id)["status"] == "failed"
        assert client.post(f"/api/v1/documents/{document_id}/retry").status_code == 200
        assert wait_document(client, document_id)["status"] == "ready"
        assert calls == [document_id, document_id]


def test_restart_recovery_and_auth(settings):
    settings.api_key = "private-key"
    # SecretStr coercion through validation, as settings are normally loaded from environment.
    settings = type(settings).model_validate(settings.model_dump())
    repo = Repository(settings.data_dir)
    doc = Document(
        document_id=str(uuid4()),
        filename="x.pdf",
        source_sha256="abc",
        status="parsing",
        index_status="uploading",
    )
    repo.save(doc)
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/v1/documents").status_code == 401
        response = client.get("/api/v1/documents", headers={"X-API-Key": "private-key"})
        assert response.json()["items"][0]["status"] == "failed"
        assert response.json()["items"][0]["index_status"] == "unknown"
        status = client.get("/api/v1/status", headers={"X-API-Key": "private-key"})
        assert "private-key" not in status.text


def test_schema_strict_and_unconfigured(settings):
    settings.ragflow_dataset_ids = ""
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/v1/context", json={"query": "hello"}).status_code == 503
        assert client.post("/api/v1/context", json={"query": " "}).status_code == 422
        assert (
            client.post("/api/v1/context", json={"query": "hello", "unknown": True}).status_code
            == 422
        )
        assert (
            client.post("/api/v1/context", json={"query": "hello", "top_k": 0}).status_code == 422
        )
        assert client.get("/openapi.json").json()["info"]["version"] == "0.0.1"
