import pytest
from fastapi.testclient import TestClient

from multiparserag.intent import resolve_intent
from multiparserag.main import create_app
from multiparserag.models import ContextRequest


@pytest.mark.parametrize(
    ("query", "intent", "retrieval_query"),
    [
        ("请总结一下：季度报告的风险", "summarize", "季度报告的风险"),
        ("Give me an overview of .NET release changes.", "summarize", ".NET release changes."),
        ("比较方案 A 和方案 B", "compare", "方案 A 和方案 B"),
        ("A 与 B 的区别是什么？", "compare", "A 与 B 的区别是什么？"),
        ("请问，如何创建备份？", "how_to", "请问，如何创建备份？"),
        ("How can I recover my account?", "how_to", "How can I recover my account?"),
        ("比较高的成本项有哪些？", "lookup", "比较高的成本项有哪些？"),
        ("文档的截止日期是何时？", "lookup", "文档的截止日期是何时？"),
        ("请总结一下！", "summarize", "请总结一下！"),
        ("  Hi  there!  ", "small_talk", None),
        ("谢谢！", "small_talk", None),
        ("您好，文档有几章？", "lookup", "您好，文档有几章？"),
    ],
)
def test_intent_is_inferred_from_query(query, intent, retrieval_query):
    assert resolve_intent(ContextRequest(query=query)) == (intent, retrieval_query)


def test_context_rewrites_only_the_query_for_retrieval(settings, monkeypatch):
    app = create_app(settings)
    body = {
        "query": "请对比：方案 A 与方案 B",
        "dataset_ids": ["kb-1"],
        "document_ids": ["doc-key"],
        "top_k": 3,
        "similarity_threshold": 0.4,
        "max_context_chars": 550,
    }

    def retrieve(request):
        assert request == ContextRequest(**{**body, "query": "方案 A 与方案 B"})
        return request.dataset_ids, []

    monkeypatch.setattr(app.state.pipeline, "retrieve", retrieve)
    with TestClient(app) as client:
        response = client.post("/api/v1/context", json=body)

    assert response.status_code == 200
    result = response.json()
    assert result["query"] == body["query"]
    assert result["intent"] == "compare"
    assert result["retrieval_query"] == "方案 A 与方案 B"
    assert result["missing_information"]


def test_small_talk_skips_retrieval_and_keeps_dataset_permissions(settings, monkeypatch):
    settings = settings.model_copy(update={"milvus_dataset_ids": "kb-1", "embedding_model": ""})
    app = create_app(settings)

    def retrieve(_request):
        pytest.fail("Small talk must bypass retrieval")

    monkeypatch.setattr(app.state.pipeline, "retrieve", retrieve)
    with TestClient(app) as client:
        response = client.post("/api/v1/context", json={"query": "您好！"})
        allowed = client.post("/api/v1/context", json={"query": "您好！", "dataset_ids": ["kb-1"]})
        forbidden = client.post(
            "/api/v1/context", json={"query": "您好！", "dataset_ids": ["private"]}
        )

    assert response.status_code == 200
    result = response.json()
    assert result["intent"] == "small_talk"
    assert result["retrieval_query"] is None
    assert result["context_text"] == ""
    assert result["evidence"] == []
    assert result["missing_information"] == []
    assert allowed.status_code == 200
    assert allowed.json()["applied_filters"]["dataset_ids"] == ["kb-1"]
    assert forbidden.status_code == 403


def test_explicit_intent_overrides_classification_and_rejects_invalid_values(settings, monkeypatch):
    app = create_app(settings)

    def retrieve(request):
        assert request.query == "你好！"
        return [], []

    monkeypatch.setattr(app.state.pipeline, "retrieve", retrieve)
    with TestClient(app) as client:
        response = client.post("/api/v1/context", json={"query": "你好！", "intent": "lookup"})
        invalid = client.post("/api/v1/context", json={"query": "你好！", "intent": "anything"})

    assert response.status_code == 200
    assert response.json()["intent"] == "lookup"
    assert response.json()["retrieval_query"] == "你好！"
    assert invalid.status_code == 422
