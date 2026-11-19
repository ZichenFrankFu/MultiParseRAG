"""Opt-in real-service acceptance: PDF -> MinerU -> images -> optional RAGFlow."""

import argparse
import time
from pathlib import Path

import httpx

from multiparserag.config import Settings

parser = argparse.ArgumentParser()
parser.add_argument("pdf", type=Path)
parser.add_argument("--base-url", default="http://localhost:8080")
parser.add_argument("--dataset-id")
parser.add_argument("--query", default="请概括这份文档的主要内容")
parser.add_argument("--require-images", action="store_true")
args = parser.parse_args()
settings = Settings()
headers = {"X-API-Key": settings.api_key.get_secret_value()}
with httpx.Client(base_url=args.base_url, headers=headers, timeout=90) as client:
    with args.pdf.open("rb") as file:
        response = client.post("/api/v1/documents", files={"file": (args.pdf.name, file)})
    response.raise_for_status()
    doc = response.json()
    document_id = doc["document_id"]
    deadline = time.monotonic() + settings.parse_timeout_seconds + 180
    while doc["status"] not in {"ready", "failed"} and time.monotonic() < deadline:
        time.sleep(2)
        response = client.get(f"/api/v1/documents/{document_id}")
        response.raise_for_status()
        doc = response.json()
    if doc["status"] != "ready":
        raise SystemExit(f"Parse failed: {doc.get('error') or 'timed out'}")
    if not doc["markdown"].strip():
        raise SystemExit("Markdown is empty")
    if args.require_images and not doc["assets"]:
        raise SystemExit("Expected at least one extracted image")
    # Deliberately omit API credentials: public browser URLs must work anonymously.
    for asset in doc["assets"]:
        image = httpx.get(asset["url"], timeout=30)
        image.raise_for_status()
        if not image.headers.get("content-type", "").startswith("image/"):
            raise SystemExit("Asset URL does not return an image")
    print(
        f"PASS: document={document_id}, assets={len(doc['assets'])}, anonymous image GET verified"
    )
    if args.dataset_id:
        response = client.post(
            f"/api/v1/documents/{document_id}/index", json={"dataset_id": args.dataset_id}
        )
        response.raise_for_status()
        deadline = time.monotonic() + 600
        doc = response.json()
        while doc["index_status"] == "indexing" and time.monotonic() < deadline:
            time.sleep(3)
            response = client.post(f"/api/v1/documents/{document_id}/index/refresh")
            response.raise_for_status()
            doc = response.json()
        if doc["index_status"] != "indexed":
            raise SystemExit(f"Index incomplete: {doc['index_status']}")
        response = client.post(
            "/api/v1/context",
            json={
                "query": args.query,
                "dataset_ids": [args.dataset_id],
                "document_ids": [doc["ragflow_document_id"]],
                "similarity_threshold": 0,
            },
        )
        response.raise_for_status()
        if not response.json()["evidence"]:
            raise SystemExit("No evidence returned from the indexed document")
        print("PASS: RAGFlow indexing and evidence retrieval verified")
