import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from app.worker import extractor
from app.worker.extractor import is_transient_error, parse_tool_use_response, process_document


def make_client_error(code, status=400):
    return ClientError(
        {
            "Error": {"Code": code, "Message": "boom"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "Converse",
    )


def test_throttling_is_transient():
    assert is_transient_error(make_client_error("ThrottlingException")) is True


def test_server_error_is_transient():
    assert is_transient_error(make_client_error("SomethingElse", status=503)) is True


def test_connection_error_is_transient():
    assert is_transient_error(EndpointConnectionError(endpoint_url="http://x")) is True


def test_validation_error_is_permanent():
    assert is_transient_error(make_client_error("ValidationException")) is False


def test_plain_exception_is_permanent():
    assert is_transient_error(ValueError("bad data")) is False


def test_parse_tool_use_response():
    response = {
        "output": {
            "message": {
                "content": [
                    {"text": "thinking..."},
                    {"toolUse": {"input": {"document_type": "invoice", "fields": {"total": 42}}}},
                ]
            }
        }
    }
    assert parse_tool_use_response(response) == {
        "document_type": "invoice",
        "fields": {"total": 42},
    }


def test_parse_tool_use_response_missing_block():
    response = {"output": {"message": {"content": [{"text": "no tool call"}]}}}
    with pytest.raises(ValueError):
        parse_tool_use_response(response)


@pytest.fixture
def happy_pipeline(monkeypatch):
    statuses = []
    monkeypatch.setattr(
        extractor, "update_document_status",
        lambda doc_id, status, error=None: statuses.append(status),
    )
    monkeypatch.setattr(extractor, "download_document_from_s3", lambda key: b"%PDF-fake")
    monkeypatch.setattr(
        extractor, "extract_document_with_claude",
        lambda data, name: {
            "extracted_data": {"document_type": "invoice", "fields": {}},
            "input_tokens": 10,
            "output_tokens": 5,
            "model_id": "test-model",
        },
    )
    monkeypatch.setattr(extractor, "save_extraction_to_dynamodb", lambda doc_id, result: "invoice")
    monkeypatch.setattr(extractor, "trigger_kb_ingestion", lambda doc_id, key: "INDEXED")
    return statuses


def test_process_document_completed(happy_pipeline):
    outcome = process_document("doc-1", "uploads/doc-1/a.pdf", "a.pdf")
    assert outcome == "completed"
    assert happy_pipeline == ["PROCESSING", "COMPLETED"]


def test_process_document_transient_failure_retries(happy_pipeline, monkeypatch):
    def raise_throttle(data, name):
        raise make_client_error("ThrottlingException")

    monkeypatch.setattr(extractor, "extract_document_with_claude", raise_throttle)
    outcome = process_document("doc-1", "uploads/doc-1/a.pdf", "a.pdf")
    assert outcome == "retry"
    assert happy_pipeline == ["PROCESSING", "PENDING"]


def test_process_document_permanent_failure(happy_pipeline, monkeypatch):
    def raise_bad_response(data, name):
        raise ValueError("Model response contained no toolUse block")

    monkeypatch.setattr(extractor, "extract_document_with_claude", raise_bad_response)
    outcome = process_document("doc-1", "uploads/doc-1/a.pdf", "a.pdf")
    assert outcome == "failed"
    assert happy_pipeline == ["PROCESSING", "FAILED"]
