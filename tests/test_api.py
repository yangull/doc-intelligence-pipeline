import uuid
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from main import app
from app.api import documents
from app.core.config import settings

client = TestClient(app)


@pytest.fixture
def fake_aws(monkeypatch):
    s3 = MagicMock()
    s3.generate_presigned_url.return_value = "https://example.com/presigned-put"
    table = MagicMock()
    dynamodb = MagicMock()
    dynamodb.Table.return_value = table
    monkeypatch.setattr(documents, "get_s3_client", lambda: s3)
    monkeypatch.setattr(documents, "get_dynamodb_resource", lambda: dynamodb)
    return {"s3": s3, "table": table}


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_upload_returns_presigned_url(fake_aws):
    response = client.post(
        "/api/v1/documents/upload",
        json={"filename": "invoice.pdf", "content_type": "application/pdf"},
    )
    assert response.status_code == 200
    body = response.json()
    uuid.UUID(body["document_id"])
    assert body["upload_url"] == "https://example.com/presigned-put"
    fake_aws["table"].put_item.assert_called_once()
    item = fake_aws["table"].put_item.call_args.kwargs["Item"]
    assert item["status"] == "PENDING"
    assert item["s3_key"] == f"uploads/{body['document_id']}/invoice.pdf"


def test_status_not_found(fake_aws):
    fake_aws["table"].get_item.return_value = {}
    response = client.get("/api/v1/documents/some-id/status")
    assert response.status_code == 404


def test_status_found(fake_aws):
    fake_aws["table"].get_item.return_value = {
        "Item": {
            "document_id": "doc-1",
            "status": "COMPLETED",
            "filename": "invoice.pdf",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    }
    response = client.get("/api/v1/documents/doc-1/status")
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"


def test_query_parses_document_id_from_s3_uri(monkeypatch):
    monkeypatch.setattr(
        documents, "run_query_pipeline",
        lambda q: {
            "answer": "The total is 42.",
            "retrieved_chunks": [
                {"text": "total: 42", "source": "s3://bucket/uploads/doc-9/invoice.pdf"},
                {"text": "no uri here", "source": ""},
            ],
            "cited_chunk_indices": [1, 2],
        },
    )
    response = client.post("/api/v1/documents/query", json={"question": "What is the total?"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "The total is 42."
    assert body["sources"][0]["document_id"] == "doc-9"
    assert body["sources"][1]["document_id"] == "source-1"


def test_query_returns_only_the_chunks_the_answer_cites(monkeypatch):
    # Retrieval always returns 5 chunks; reporting all of them as "sources"
    # overstates what the answer was actually drawn from
    monkeypatch.setattr(
        documents, "run_query_pipeline",
        lambda q: {
            "answer": "890.00 EUR.",
            "retrieved_chunks": [
                {"text": "wrong invoice", "source": "s3://bucket/uploads/doc-1/a.pdf"},
                {"text": "total: 890.00", "source": "s3://bucket/uploads/doc-2/b.pdf"},
                {"text": "unrelated", "source": "s3://bucket/uploads/doc-3/c.pdf"},
            ],
            "cited_chunk_indices": [2],
        },
    )
    response = client.post("/api/v1/documents/query", json={"question": "NW-2403 total?"})
    assert response.status_code == 200
    sources = response.json()["sources"]
    assert len(sources) == 1
    assert sources[0]["document_id"] == "doc-2"
    assert sources[0]["excerpt"] == "total: 890.00"


def test_query_returns_no_sources_when_the_model_cites_nothing(monkeypatch):
    # Abstention: retrieval still returns its nearest neighbours, but the answer
    # rests on none of them, so the response must not present them as sources
    monkeypatch.setattr(
        documents, "run_query_pipeline",
        lambda q: {
            "answer": "The excerpts do not contain that information.",
            "retrieved_chunks": [
                {"text": "some invoice", "source": "s3://bucket/uploads/doc-1/a.pdf"},
                {"text": "another invoice", "source": "s3://bucket/uploads/doc-2/b.pdf"},
            ],
            "cited_chunk_indices": [],
        },
    )
    response = client.post("/api/v1/documents/query", json={"question": "Siemens total?"})
    assert response.status_code == 200
    assert response.json()["sources"] == []


def test_query_returns_502_on_malformed_model_response(monkeypatch):
    # generator() raises MalformedModelResponse when the forced tool-use response has
    # no toolUse block, is truncated, or carries no answer; the handler must translate
    # that to a clean 502, not a 500 stack trace
    def raise_malformed(question):
        raise documents.MalformedModelResponse("Model response contained no toolUse block")

    monkeypatch.setattr(documents, "run_query_pipeline", raise_malformed)
    response = client.post("/api/v1/documents/query", json={"question": "anything"})
    assert response.status_code == 502
    assert response.json()["detail"] == "Model returned a malformed response"


def test_query_does_not_mask_unrelated_value_errors(monkeypatch):
    # Regression: the handler used to catch bare ValueError, so a boto3 parameter
    # validation error or any other ValueError from inside the graph was reported to
    # the client as "Model returned a malformed response" — hiding a real server bug
    # behind a 502 that blames the model.
    def raise_unrelated(question):
        raise ValueError("Parameter validation failed: invalid knowledgeBaseId")

    monkeypatch.setattr(documents, "run_query_pipeline", raise_unrelated)
    with pytest.raises(ValueError, match="Parameter validation failed"):
        client.post("/api/v1/documents/query", json={"question": "anything"})


def test_api_key_required_when_configured(monkeypatch, fake_aws):
    monkeypatch.setattr(settings, "api_key", "secret123")

    response = client.get("/api/v1/documents/doc-1/status")
    assert response.status_code == 401

    fake_aws["table"].get_item.return_value = {}
    response = client.get(
        "/api/v1/documents/doc-1/status", headers={"X-API-Key": "secret123"}
    )
    assert response.status_code == 404


def test_health_open_without_api_key(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "secret123")
    assert client.get("/health").status_code == 200
