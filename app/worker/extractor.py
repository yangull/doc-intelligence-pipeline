import json
import base64
import boto3
from datetime import datetime, timezone
from app.core.config import settings
from app.core.aws_clients import get_s3_client, get_dynamodb_resource, get_bedrock_client
from app.schemas.documents import (
    InvoiceExtraction,
    ContractExtraction,
    ReceiptExtraction,
    UnknownExtraction,
)

# The prompt we send to Claude with every document
# We tell it exactly what we want back and in what format
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
    """Update the document status in DynamoDB."""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(settings.dynamodb_table_name)

    update_expr = "SET #s = :status, updated_at = :updated_at"
    expr_values = {
        ":status": status,
        ":updated_at": datetime.now(timezone.utc).isoformat(),
    }
    expr_names = {"#s": "status"}  # 'status' is a reserved word in DynamoDB

    # If there's an error message, save it too
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
    """Download a document from S3 and return its bytes."""
    s3_client = get_s3_client()
    response = s3_client.get_object(
        Bucket=settings.s3_bucket_name,
        Key=s3_key,
    )
    # response["Body"] is a streaming object — .read() gets all bytes
    return response["Body"].read()


def extract_document_with_claude(document_bytes: bytes, filename: str) -> dict:
    """
    Send the document to Claude via Bedrock and get back structured data.
    
    Claude accepts PDFs directly as base64-encoded content.
    We use the Converse API which is the modern unified interface.
    """
    bedrock_client = get_bedrock_client()

    # Convert bytes to base64 — this is how you send binary data in JSON
    document_base64 = base64.standard_b64encode(document_bytes).decode("utf-8")

    # Build the message with the document attached
    # Claude can read PDFs directly — no OCR needed for text-based PDFs
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "document": {
                        "name": filename.replace("-", " ").replace("_", " "),
                        "format": "pdf",
                        "source": {
                            "bytes": document_base64,
                        },
                    }
                },
                {
                    "text": EXTRACTION_PROMPT,
                },
            ],
        }
    ]

    # Call Claude via Bedrock Converse API
    response = bedrock_client.converse(
        modelId=settings.bedrock_model_id,
        messages=messages,
        inferenceConfig={
            "maxTokens": 2000,
            "temperature": 0,  # 0 = deterministic, we want consistent extraction
        },
    )

    # Parse the response
    # response["output"]["message"]["content"][0]["text"] is Claude's reply
    raw_text = response["output"]["message"]["content"][0]["text"]
    usage = response["usage"]  # token counts for cost tracking

    # Parse Claude's JSON response
    extracted_data = json.loads(raw_text)

    return {
        "extracted_data": extracted_data,
        "input_tokens": usage["inputTokens"],
        "output_tokens": usage["outputTokens"],
        "model_id": settings.bedrock_model_id,
    }


def save_extraction_to_dynamodb(document_id: str, extraction_result: dict):
    """Save Claude's extracted data to DynamoDB."""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(settings.dynamodb_table_name)

    extracted_data = extraction_result["extracted_data"]
    doc_type = extracted_data.get("doc_type", "unknown")

    # Save extraction as a separate item under the same document PK
    # PK = "DOC#<id>", SK = "EXTRACTION" — sits alongside METADATA
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
    """
    Main function — orchestrates the full extraction pipeline.
    Called by the worker when it picks up a job from SQS.
    """
    print(f"Processing document {document_id}: {filename}")

    try:
        # Step 1: Mark as processing
        update_document_status(document_id, "PROCESSING")

        # Step 2: Download from S3
        print(f"Downloading from S3: {s3_key}")
        document_bytes = download_document_from_s3(s3_key)
        print(f"Downloaded {len(document_bytes)} bytes")

        # Step 3: Send to Claude for extraction
        print("Sending to Claude for extraction...")
        extraction_result = extract_document_with_claude(document_bytes, filename)
        print(f"Extraction complete. Tokens used: {extraction_result['input_tokens']} in, {extraction_result['output_tokens']} out")

        # Step 4: Save results to DynamoDB
        doc_type = save_extraction_to_dynamodb(document_id, extraction_result)
        print(f"Saved extraction to DynamoDB. Document type: {doc_type}")

        # Step 5: Mark as completed
        update_document_status(document_id, "COMPLETED")
        print(f"Document {document_id} processing complete")

        return True

    except Exception as e:
        # If anything fails, mark as failed and save the error
        error_msg = str(e)
        print(f"Error processing document {document_id}: {error_msg}")
        update_document_status(document_id, "FAILED", error=error_msg)
        return False