import re
from hashlib import sha256
from itertools import islice
from threading import Lock

from pymilvus import DataType, MilvusClient
from pymilvus.client.types import LoadState
from pymilvus.exceptions import MilvusException

from ..config import Settings
from ..models import ContextRequest, Document, ServiceError
from .embeddings import Embeddings

RPC_TIMEOUT = 30
CHUNK_LENGTH = 1200
EMBEDDING_BATCH_SIZE = 32


def split_markdown(markdown: str) -> list[str]:
    """Keep paragraphs together when possible and bound oversized paragraphs."""
    chunks = []
    pending = ""
    for paragraph in re.split(r"\n[ \t\r]*\n", markdown.strip()):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if pending and len(pending) + 2 + len(paragraph) > CHUNK_LENGTH:
            chunks.append(pending)
            pending = ""
        start = 0
        while len(paragraph) - start > CHUNK_LENGTH:
            end = start + CHUNK_LENGTH
            cut = max(paragraph.rfind(char, start, end + 1) for char in ("\n", " ", "\t"))
            if cut - start < CHUNK_LENGTH // 2:
                cut = end
            chunks.append(paragraph[start:cut].rstrip())
            start = cut
            while start < len(paragraph) and paragraph[start].isspace():
                start += 1
        remaining = paragraph[start:]
        if remaining:
            pending = f"{pending}\n\n{remaining}" if pending else remaining
    if pending:
        chunks.append(pending)
    return chunks


class Milvus:
    """Persist document chunks and run scoped cosine search over their embeddings."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.embeddings = Embeddings(settings)
        self._client: MilvusClient | None = None
        self._client_lock = Lock()

    def datasets(self, requested: list[str]) -> list[str]:
        allowed = self.settings.dataset_ids
        if not allowed:
            raise ServiceError("请先配置 MPR_MILVUS_DATASET_IDS。", 503)
        selected = list(dict.fromkeys(requested)) if requested else allowed
        if not set(selected).issubset(allowed):
            raise ServiceError("请求包含未授权的知识库。", 403)
        return selected

    @property
    def _embedding_key(self) -> str:
        signature = f"1\n{self.embeddings.model}\n{self.settings.embedding_dimension}"
        return sha256(signature.encode()).hexdigest()

    @property
    def client(self) -> MilvusClient:
        with self._client_lock:
            if self._client is None:
                self._client = MilvusClient(
                    uri=self.settings.milvus_uri,
                    token=self.settings.milvus_token.get_secret_value(),
                    timeout=RPC_TIMEOUT,
                )
            return self._client

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def _collection(self, *, create: bool = False) -> bool:
        name = self.settings.milvus_collection
        if not self.client.has_collection(name, timeout=RPC_TIMEOUT):
            if not create:
                return False
            schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
            schema.add_field("dataset_id", DataType.VARCHAR, max_length=128)
            schema.add_field("document_id", DataType.VARCHAR, max_length=36)
            schema.add_field("title", DataType.VARCHAR, max_length=1024)
            schema.add_field("content", DataType.VARCHAR, max_length=CHUNK_LENGTH * 4)
            schema.add_field("embedding_key", DataType.VARCHAR, max_length=64)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self.settings.embedding_dimension)
            self.client.create_collection(
                collection_name=name,
                schema=schema,
                consistency_level="Strong",
                timeout=RPC_TIMEOUT,
            )

        description = self.client.describe_collection(name, timeout=RPC_TIMEOUT)
        fields = {field["name"]: field for field in description["fields"]}
        dim = fields.get("vector", {}).get("params", {}).get("dim")
        required = {"id", "dataset_id", "document_id", "title", "content", "embedding_key"}
        if not required.issubset(fields) or str(dim) != str(self.settings.embedding_dimension):
            raise ServiceError("Milvus 集合结构或向量维度不匹配，请配置新的集合。", 503)
        # A retry can finish index or load setup if the previous request was interrupted.
        if create and not self.client.list_indexes(name, field_name="vector", timeout=RPC_TIMEOUT):
            index = self.client.prepare_index_params()
            index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
            self.client.create_index(name, index, timeout=RPC_TIMEOUT)
        if self.client.get_load_state(name, timeout=RPC_TIMEOUT)["state"] != LoadState.Loaded:
            self.client.load_collection(name, timeout=RPC_TIMEOUT)
        return True

    def index(self, doc: Document, dataset_id: str) -> None:
        chunks = split_markdown(doc.markdown)
        if not chunks:
            raise ServiceError("文档没有可索引的 Markdown 内容。", 409)
        model_key = self._embedding_key
        try:
            self._collection(create=True)
            rows = iter(enumerate(chunks))
            while batch := list(islice(rows, EMBEDDING_BATCH_SIZE)):
                vectors = self.embeddings.embed([content for _, content in batch])
                data = [
                    {
                        "id": sha256(
                            f"{dataset_id}\n{doc.document_id}\n{model_key}\n{number}".encode()
                        ).hexdigest(),
                        "document_id": doc.document_id,
                        "dataset_id": dataset_id,
                        "title": doc.filename,
                        "content": content,
                        "embedding_key": model_key,
                        "vector": vector,
                    }
                    for (number, content), vector in zip(batch, vectors, strict=True)
                ]
                result = self.client.upsert(
                    self.settings.milvus_collection, data, timeout=RPC_TIMEOUT
                )
                if result.get("upsert_count") != len(data):
                    raise ServiceError("Milvus 未确认全部向量写入，请重试索引。", 503)
        except MilvusException as exc:
            raise ServiceError("Milvus 索引写入失败，请检查服务与连接配置。", 503) from exc

    def is_indexed(self, doc: Document) -> bool:
        model_key = self._embedding_key
        expected = len(split_markdown(doc.markdown))
        if not expected:
            return False
        try:
            if not self._collection():
                return False
            counts = self.client.query(
                collection_name=self.settings.milvus_collection,
                filter=(
                    "dataset_id == {dataset} and document_id == {document} "
                    "and embedding_key == {embedding_key}"
                ),
                filter_params={
                    "dataset": doc.dataset_id,
                    "document": doc.document_id,
                    "embedding_key": model_key,
                },
                output_fields=["count(*)"],
                consistency_level="Strong",
                timeout=RPC_TIMEOUT,
            )
        except MilvusException as exc:
            raise ServiceError("Milvus 索引状态读取失败，请检查服务与连接配置。", 503) from exc
        return bool(counts) and counts[0].get("count(*)") == expected

    def retrieve(
        self, request: ContextRequest, datasets: list[str], document_ids: list[str]
    ) -> list[dict]:
        model_key = self._embedding_key
        if not document_ids:
            return []
        try:
            if not self._collection():
                return []
            results = self.client.search(
                collection_name=self.settings.milvus_collection,
                data=self.embeddings.embed([request.query]),
                anns_field="vector",
                search_params={"metric_type": "COSINE"},
                filter=(
                    "dataset_id in {datasets} and document_id in {documents} "
                    "and embedding_key == {embedding_key}"
                ),
                filter_params={
                    "datasets": datasets,
                    "documents": document_ids,
                    "embedding_key": model_key,
                },
                limit=request.top_k,
                output_fields=["document_id", "dataset_id", "title", "content"],
                consistency_level="Strong",
                timeout=RPC_TIMEOUT,
            )
        except MilvusException as exc:
            raise ServiceError("Milvus 检索失败，请检查服务与连接配置。", 503) from exc
        return [
            {**hit["entity"], "id": hit["id"], "similarity": hit["distance"]} for hit in results[0]
        ]
