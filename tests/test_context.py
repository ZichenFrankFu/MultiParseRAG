from uuid import uuid4

import pytest

from multiparserag.models import Asset, ContextRequest, Document, ServiceError
from multiparserag.pipeline import Pipeline
from multiparserag.repository import Repository


def test_citations_budget_asset_mapping_and_filter(settings):
    repo = Repository(settings.data_dir)
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
            document_id=str(uuid4()),
            filename="original.pdf",
            source_sha256="abc",
            status="ready",
            ragflow_document_id="remote",
            dataset_id="kb-1",
            assets=[asset],
        )
    )
    pipeline = Pipeline(settings, repo)
    chunk = {
        "id": "chunk1",
        "document_id": "remote",
        "dataset_id": "kb-1",
        "similarity": 0.8,
        "document_keyword": "original.pdf",
        "content": f"fact ![figure]({asset.url}) " + "x" * 1000,
    }
    pipeline.ragflow.retrieve = lambda req: (
        ["kb-1"],
        [
            {**chunk, "id": "outside", "dataset_id": "private"},
            {**chunk, "id": "weak", "similarity": 0.1},
            chunk,
            chunk,
        ],
    )
    result = pipeline.context(ContextRequest(query="fact", max_context_chars=500))
    assert len(result.context_text) == 500
    assert result.truncated
    assert len(result.evidence) == 1
    assert result.evidence[0].citation_id == "E1"
    assert result.evidence[0].local_document_id
    assert result.evidence[0].asset_ids == ["asset"]
    assert result.assets[0].url == asset.url
    assert result.context_text.startswith("[E1] original.pdf\n")


def test_empty_result_is_explicit(settings):
    pipeline = Pipeline(settings, Repository(settings.data_dir))
    pipeline.ragflow.retrieve = lambda req: (["kb-1"], [])
    result = pipeline.context(ContextRequest(query="missing"))
    assert result.context_text == ""
    assert result.evidence == []
    assert result.missing_information


def test_index_retry_reuses_upload(settings):
    repo = Repository(settings.data_dir)
    document_id = str(uuid4())
    repo.save(
        Document(
            document_id=document_id,
            filename="source.pdf",
            source_sha256="abc",
            status="ready",
            markdown="fact",
        )
    )
    pipeline = Pipeline(settings, repo)
    uploads = []

    def upload(*args):
        uploads.append(args)
        return "remote-id"

    pipeline.ragflow.upload = upload

    def fail(*args):
        raise ServiceError("parse failed")

    pipeline.ragflow.parse = fail
    with pytest.raises(ServiceError):
        pipeline.index(document_id, "kb-1")
    assert repo.get(document_id).ragflow_document_id == "remote-id"
    pipeline.ragflow.parse = lambda *args: None
    assert pipeline.index(document_id, "kb-1").index_status == "indexing"
    assert pipeline.index(document_id, "kb-1").index_status == "indexing"
    assert len(uploads) == 1
    pipeline.ragflow.document = lambda *args: {"run": "DONE"}
    assert pipeline.refresh_index(document_id).index_status == "indexed"


def test_upload_unknown_blocks_duplicate(settings):
    repo = Repository(settings.data_dir)
    document_id = str(uuid4())
    repo.save(
        Document(
            document_id=document_id,
            filename="source.pdf",
            source_sha256="abc",
            status="ready",
            markdown="fact",
        )
    )
    pipeline = Pipeline(settings, repo)

    def fail(*args):
        raise ServiceError("response lost", outcome_unknown=True)

    pipeline.ragflow.upload = fail
    with pytest.raises(ServiceError):
        pipeline.index(document_id, "kb-1")
    assert repo.get(document_id).index_status == "unknown"
    with pytest.raises(ServiceError) as error:
        pipeline.index(document_id, "kb-1")
    assert error.value.status_code == 409


@pytest.mark.parametrize(
    "run,expected",
    [
        ("3", "indexed"),
        ("DONE", "indexed"),
        ("FAIL", "failed"),
        ("CANCEL", "failed"),
        ("RUNNING", "indexing"),
    ],
)
def test_ragflow_numeric_and_text_states(settings, run, expected):
    repo = Repository(settings.data_dir)
    document_id = str(uuid4())
    repo.save(
        Document(
            document_id=document_id,
            filename="source.pdf",
            source_sha256="abc",
            status="ready",
            ragflow_document_id="remote",
            dataset_id="kb-1",
        )
    )
    pipeline = Pipeline(settings, repo)
    pipeline.ragflow.document = lambda *args: {"run": run}
    assert pipeline.refresh_index(document_id).index_status == expected


def test_confirmed_upload_error_can_retry(settings):
    repo = Repository(settings.data_dir)
    document_id = str(uuid4())
    repo.save(
        Document(
            document_id=document_id,
            filename="source.pdf",
            source_sha256="abc",
            status="ready",
            markdown="fact",
        )
    )
    pipeline = Pipeline(settings, repo)

    def fail(*args):
        raise ServiceError("Invalid API key")

    pipeline.ragflow.upload = fail
    with pytest.raises(ServiceError):
        pipeline.index(document_id, "kb-1")
    assert repo.get(document_id).index_status == "failed"
    pipeline.ragflow.upload = lambda *args: "remote-id"
    pipeline.ragflow.parse = lambda *args: None
    assert pipeline.index(document_id, "kb-1").index_status == "indexing"
