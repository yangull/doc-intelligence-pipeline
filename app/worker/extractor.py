import json
import boto3
from decimal import Decimal
from datetime import datetime, timezone
from app.core.config import settings
from app.core.aws_clients import get_s3_client, get_dynamodb_resource, get_bedrock_client

EXTRACTION_PROMPT = """You are a document intelligence assistant. Analyze the provided document and extract structured information from it.

First, identify the document type:
- invoice: a bill for goods or services
- contract: a legal agreement between parties  
- receipt: proof of a purchase transaction
- unknown: anything else

Then extract all relevant fields based on the document type.

You must respond with valid JSON only. No explanation, no markdown, no extra text.
Just the raw JSON object."""


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


def extract_document_with_claude(document_bytes: bytes, filename: str) -> dict:
    bedrock_client = get_bedrock_client()
    clean_filename = filename.rsplit(".", 1)[0].replace("-", " ").replace("_", " ")
    print(f"Using clean filename: '{clean_filename}'")

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
    )

    raw_text = response["output"]["message"]["content"][0]["text"]
    usage = response["usage"]
    print(f"Claude raw response: {repr(raw_text)}")

    clean_text = raw_text.strip()
    if clean_text.startswith("```"):
        clean_text = clean_text.split("\n", 1)[1]
        clean_text = clean_text.rsplit("```", 1)[0]
        clean_text = clean_text.strip()

    extracted_data = json.loads(clean_text)

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
    doc_type = extracted_data.get("doc_type", "unknown")

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


def process_document(document_id: str, s3_key: str, filename: str):
    print(f"Processing document {document_id}: {filename}")
    try:
        update_document_status(document_id, "PROCESSING")

        print(f"Downloading from S3: {s3_key}")
        document_bytes = download_document_from_s3(s3_key)
        print(f"Downloaded {len(document_bytes)} bytes")

        print("Sending to Claude for extraction...")
        extraction_result = extract_document_with_claude(document_bytes, filename)
        print(f"Extraction complete. Tokens used: {extraction_result['input_tokens']} in, {extraction_result['output_tokens']} out")

        doc_type = save_extraction_to_dynamodb(document_id, extraction_result)
        print(f"Saved extraction to DynamoDB. Document type: {doc_type}")

        update_document_status(document_id, "COMPLETED")
        print(f"Document {document_id} processing complete")
        return True

    except Exception as e:
        error_msg = str(e)
        print(f"Error processing document {document_id}: {error_msg}")
        update_document_status(document_id, "FAILED", error=error_msg)
        return False