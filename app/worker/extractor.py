import json
import logging
from decimal import Decimal
from datetime import datetime, timezone
from botocore.exceptions import BotoCoreError, ClientError
from app.core.config import settings
from app.core.aws_clients import get_s3_client, get_dynamodb_resource, get_bedrock_client, get_bedrock_agent_client

logger = logging.getLogger(__name__)


EXTRACTION_PROMPT = """You are a document intelligence assistant. Analyze the provided document.

First, identify the document type:
- invoice: a bill for goods or services
- contract: a legal agreement between parties
- receipt: proof of a purchase transaction
- unknown: anything else

Then record the document type and all relevant fields using the record_extraction tool."""

# Forced tool use gives schema-validated JSON — no markdown fences to strip
EXTRACTION_TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "record_extraction",
                "description": "Record structured data extracted from a document.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "document_type": {
                                "type": "string",
                                "enum": ["invoice", "contract", "receipt", "unknown"],
                            },
                            "fields": {
                                "type": "object",
                                "description": "All extracted fields relevant to the document type",
                            },
                        },
                        "required": ["document_type", "fields"],
                    }
                },
            }
        }
    ],
    "toolChoice": {"tool": {"name": "record_extraction"}},
}

# Error codes worth retrying via SQS redelivery; anything else is permanent
TRANSIENT_ERROR_CODES = {
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelTimeoutException",
    "ModelNotReadyException",
    "RequestTimeout",
    "SlowDown",
}


def is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        return code in TRANSIENT_ERROR_CODES or status >= 500
    # Connection resets, timeouts, endpoint hiccups
    return isinstance(exc, BotoCoreError)


def update_document_status(document_id: str, status: str, error: str = None):
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(settings.dynamodb_table_name)
    update_expr = "SET #s = :status, updated_at = :updated_at"
    expr_values = {
        ":status": status,
        ":updated_at": datetime.now(timezone.utc).isoformat(),
    }
    expr_names = {"#s": "status"}
    if error:
        update_expr += ", error_message = :error"
        expr_values[":error"] = error
    table.update_item(
        Key={"PK": f"DOC#{document_id}", "SK": "METADATA"},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
        ExpressionAttributeNames=expr_names,
    )


def download_document_from_s3(s3_key: str) -> bytes:
    s3_client = get_s3_client()
    response = s3_client.get_object(
        Bucket=settings.s3_bucket_name,
        Key=s3_key,
    )
    return response["Body"].read()


def parse_tool_use_response(response: dict) -> dict:
    for block in response["output"]["message"]["content"]:
        if "toolUse" in block:
            return block["toolUse"]["input"]
    raise ValueError("Model response contained no toolUse block")


def extract_document_with_claude(document_bytes: bytes, filename: str) -> dict:
    bedrock_client = get_bedrock_client()
    clean_filename = filename.rsplit(".", 1)[0].replace("-", " ").replace("_", " ")

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "document": {
                        "name": clean_filename,
                        "format": "pdf",
                        "source": {
                            "bytes": document_bytes,
                        },
                    }
                },
                {
                    "text": EXTRACTION_PROMPT,
                },
            ],
        }
    ]

    response = bedrock_client.converse(
        modelId=settings.bedrock_model_id,
        messages=messages,
        inferenceConfig={
            "maxTokens": 2000,
            "temperature": 0,
        },
        toolConfig=EXTRACTION_TOOL_CONFIG,
    )

    extracted_data = parse_tool_use_response(response)
    usage = response["usage"]

    return {
        "extracted_data": extracted_data,
        "input_tokens": usage["inputTokens"],
        "output_tokens": usage["outputTokens"],
        "model_id": settings.bedrock_model_id,
    }


def save_extraction_to_dynamodb(document_id: str, extraction_result: dict):
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(settings.dynamodb_table_name)
    extracted_data = extraction_result["extracted_data"]
    doc_type = extracted_data.get("document_type", "unknown")

    # DynamoDB doesn't support float — convert all floats to Decimal
    extraction_str = json.dumps(extracted_data)
    extracted_data = json.loads(extraction_str, parse_float=Decimal)

    table.put_item(Item={
        "PK": f"DOC#{document_id}",
        "SK": "EXTRACTION",
        "document_id": document_id,
        "document_type": doc_type,
        "extraction": extracted_data,
        "model_id": extraction_result["model_id"],
        "input_tokens": extraction_result["input_tokens"],
        "output_tokens": extraction_result["output_tokens"],
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    })
    return doc_type


def trigger_kb_ingestion(s3_key: str):
    # Document-level ingestion indexes just this file instead of
    # rescanning the whole data source like start_ingestion_job.
    #
    # No metadata block: the data source is S3-backed, and Bedrock rejects
    # IN_LINE_ATTRIBUTE metadata for those with "You can only upload content and
    # metadata from S3 if your knowledge base is connected to an S3 data source".
    # Attaching document_id would mean writing a sidecar .metadata.json next to
    # every upload, which is the design this repo deliberately moved away from.
    # The S3 key already carries the document id, so nothing is lost downstream.
    bedrock_agent = get_bedrock_agent_client()
    response = bedrock_agent.ingest_knowledge_base_documents(
        knowledgeBaseId=settings.bedrock_kb_id,
        dataSourceId=settings.bedrock_kb_data_source_id,
        documents=[
            {
                "content": {
                    "dataSourceType": "S3",
                    "s3": {
                        "s3Location": {"uri": f"s3://{settings.s3_bucket_name}/{s3_key}"}
                    },
                },
            }
        ],
    )
    status = response["documentDetails"][0]["status"]
    logger.info("KB ingestion for %s: %s", s3_key, status)
    return status


def process_document(document_id: str, s3_key: str, filename: str) -> str:
    """Returns 'completed', 'retry' (transient failure), or 'failed' (permanent)."""
    logger.info("Processing document %s: %s", document_id, filename)
    try:
        update_document_status(document_id, "PROCESSING")

        document_bytes = download_document_from_s3(s3_key)
        logger.info("Downloaded %d bytes from %s", len(document_bytes), s3_key)

        extraction_result = extract_document_with_claude(document_bytes, filename)
        logger.info(
            "Extraction complete. Tokens used: %d in, %d out",
            extraction_result["input_tokens"],
            extraction_result["output_tokens"],
        )

        doc_type = save_extraction_to_dynamodb(document_id, extraction_result)
        logger.info("Saved extraction to DynamoDB. Document type: %s", doc_type)
        trigger_kb_ingestion(s3_key)

        update_document_status(document_id, "COMPLETED")
        logger.info("Document %s processing complete", document_id)
        return "completed"

    except Exception as e:
        if is_transient_error(e):
            logger.warning("Transient error processing %s, will retry: %s", document_id, e)
            update_document_status(document_id, "PENDING", error=str(e))
            return "retry"
        logger.error("Permanent error processing %s: %s", document_id, e)
        update_document_status(document_id, "FAILED", error=str(e))
        return "failed"
