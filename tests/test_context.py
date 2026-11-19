from uuid import uuid4

import pytest

from multiparserag.models import Asset, ContextRequest, Document, ServiceError
from multiparserag.pipeline import Pipeline
from multiparserag.repository import Repository


def test_citations_budget_asset_mapping_and_filter(settings):
    repo = Repository(settings.data_dir)
    document_id = str(uuid4())
    asset = Asset(
        asset_id="asset",
        original_path="images/a.png",
        object_key="doc/a.png",
        url="https://assets.example/a.png",
        media_type="image/png",
        sha256="abc",
        size_bytes=12,
    )
    repo.save(
        Document(
            document_id=document_id,
            filename="original.pdf",
            source_sha256="abc",
            status="ready",
            ragflow_document_id="remote",
            dataset_id="kb-1",
            assets=[asset],
            index_provider="milvus",
            index_status="indexed",
        )
    )
    pipeline = Pipeline(settings, repo)
    chunk = {
        "id": "chunk1",
        "document_id": document_id,
        "dataset_id": "kb-1",
        "similarity": 0.8,
        "title": "original.pdf",
        "content": f"fact ![figure]({asset.url}) " + "x" * 1000,
    }

    def retrieve(req, datasets, document_ids):
        assert datasets == ["kb-1"]
        assert document_ids == [document_id]
        return [
            {**chunk, "id": "outside", "dataset_id": "private"},
            {**chunk, "id": "weak", "similarity": 0.1},
            chunk,
            chunk,
        ]

    pipeline.vectors.retrieve = retrieve
    result = pipeline.context(
        ContextRequest(query="fact", document_ids=["remote"], max_context_chars=500)
    )
    assert len(result.context_text) == 500
    assert result.truncated
    assert len(result.evidence) == 1
    assert result.evidence[0].citation_id == "E1"
    assert result.evidence[0].local_document_id == document_id
    assert result.evidence[0].asset_ids == ["asset"]
    assert result.assets[0].url == asset.url
    assert result.context_text.startswith("[E1] original.pdf\n")


def test_empty_result_is_explicit(settings):
    pipeline = Pipeline(settings, Repository(settings.data_dir))
    result = pipeline.context(ContextRequest(query="missing"))
    assert result.context_text == ""
    assert result.evidence == []
    assert result.missing_information


def test_legacy_document_can_be_reindexed_and_failed_index_can_retry(settings, monkeypatch):
    repo = Repository(settings.data_dir)
    document_id = str(uuid4())
    repo.save(
        Document(
            document_id=document_id,
            filename="source.pdf",
            source_sha256="abc",
            status="ready",
            markdown="fact",
            index_status="indexed",
            dataset_id="legacy-dataset",
            ragflow_document_id="remote-id",
        )
    )
    pipeline = Pipeline(settings, repo)
    repo.recover()
    assert repo.get(document_id).index_status == "not_indexed"
    attempts = []

    def put(document, dataset_id):
        attempts.append((document.document_id, dataset_id))
        if len(attempts) == 1:
            raise ServiceError("vector store unavailable")

    monkeypatch.setattr(pipeline.vectors, "index", put)
    with pytest.raises(ServiceError, match="unavailable"):
        pipeline.index(document_id, "kb-1")
    failed = repo.get(document_id)
    assert failed.index_status == "failed"
    assert failed.index_error == "vector store unavailable"
    assert failed.ragflow_document_id == "remote-id"

    monkeypatch.setattr(pipeline.vectors, "is_indexed", lambda doc: True)
    indexed = pipeline.index(document_id, "kb-1")
    assert indexed.index_provider == "milvus"
    assert indexed.index_status == "indexed"
    assert indexed.dataset_id == "kb-1"
    assert pipeline.index(document_id, "kb-1").index_status == "indexed"
    assert attempts == [(document_id, "kb-1"), (document_id, "kb-1")]
    assert pipeline.refresh_index(document_id).index_status == "indexed"


def test_refresh_allows_missing_index_to_be_rebuilt(settings, monkeypatch):
    repo = Repository(settings.data_dir)
    document_id = str(uuid4())
    repo.save(
        Document(
            document_id=document_id,
            filename="source.pdf",
            source_sha256="abc",
            status="ready",
            markdown="fact",
            dataset_id="kb-1",
            index_provider="milvus",
            index_status="indexed",
        )
    )
    pipeline = Pipeline(settings, repo)
    monkeypatch.setattr(pipeline.vectors, "is_indexed", lambda doc: False)
    refreshed = pipeline.refresh_index(document_id)
    assert refreshed.index_status == "failed"
    assert refreshed.index_error
    monkeypatch.setattr(pipeline.vectors, "index", lambda doc, dataset_id: None)
    assert pipeline.index(document_id, "kb-1").index_status == "indexed"


def test_partial_index_is_not_searched_after_restart(settings, monkeypatch):
    repo = Repository(settings.data_dir)
    document_id = str(uuid4())
    repo.save(
        Document(
            document_id=document_id,
            filename="source.pdf",
            source_sha256="abc",
            status="ready",
            markdown="fact",
            dataset_id="kb-1",
            index_provider="milvus",
            index_status="indexing",
        )
    )
    repo.recover()
    assert repo.get(document_id).index_status == "failed"
    pipeline = Pipeline(settings, repo)

    def retrieve(req, datasets, document_ids):
        assert document_ids == []
        return []

    monkeypatch.setattr(pipeline.vectors, "retrieve", retrieve)
    assert not pipeline.context(ContextRequest(query="fact")).evidence
