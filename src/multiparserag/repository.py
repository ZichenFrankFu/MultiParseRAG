"""Small single-process catalog; source files and parse artifacts live on disk."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from .models import Document, now


class Repository:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "catalog.sqlite3"
        self.lock = RLock()
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, document: Document):
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT INTO documents VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                (document.document_id, document.model_dump_json()),
            )

    def get(self, document_id: str) -> Document | None:
        with self.connect() as db:
            row = db.execute("SELECT body FROM documents WHERE id=?", (document_id,)).fetchone()
        return Document.model_validate_json(row[0]) if row else None

    def list(self) -> list[Document]:
        with self.connect() as db:
            rows = db.execute("SELECT body FROM documents ORDER BY rowid DESC").fetchall()
        return [Document.model_validate_json(row[0]) for row in rows]

    def update(self, document_id: str, **changes) -> Document:
        with self.lock:
            document = self.get(document_id)
            if document is None:
                raise KeyError(document_id)
            data = document.model_dump()
            data.update(changes, updated_at=now())
            updated = Document.model_validate(data)
            self.save(updated)
            return updated

    def recover(self):
        for doc in self.list():
            if doc.status in {"parsing", "publishing"}:
                self.update(
                    doc.document_id, status="failed", error="进程中断，可重试同一解析任务。"
                )
            if doc.index_status == "uploading":
                self.update(
                    doc.document_id,
                    index_status="unknown",
                    index_error="上传响应未确认，请在 RAGFlow 核对后关联已有文档。",
                )
