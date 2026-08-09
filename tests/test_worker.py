import json
from app.worker.worker import parse_sqs_message


def make_message(bucket="my-bucket", key="uploads/doc-123/invoice.pdf"):
    return {
        "Body": json.dumps({
            "detail": {
                "bucket": {"name": bucket},
                "object": {"key": key},
            }
        })
    }


def test_parse_valid_message():
    result = parse_sqs_message(make_message())
    assert result == {
        "document_id": "doc-123",
        "s3_key": "uploads/doc-123/invoice.pdf",
        "filename": "invoice.pdf",
    }


def test_parse_message_missing_detail():
    assert parse_sqs_message({"Body": json.dumps({"foo": "bar"})}) is None


def test_parse_message_invalid_json():
    assert parse_sqs_message({"Body": "not json"}) is None


def test_parse_message_unexpected_key_format():
    assert parse_sqs_message(make_message(key="invoice.pdf")) is None
