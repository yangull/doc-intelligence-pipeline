import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.config import settings
from app.core.aws_clients import get_s3_client, get_dynamodb_resource

# APIRouter is like a mini-app — groups related endpoints together
# We'll register this router in main.py
router = APIRouter(prefix="/documents", tags=["documents"])


# --- Request/Response models ---

class UploadRequest(BaseModel):
    filename: str      # e.g. "invoice_january.pdf"
    content_type: str  # e.g. "application/pdf"


class UploadResponse(BaseModel):
    document_id: str   # unique ID we generate
    upload_url: str    # the presigned S3 URL the browser uploads to
    expires_in: int    # seconds until the URL expires


class DocumentStatus(BaseModel):
    document_id: str
    status: str        # PENDING, PROCESSING, COMPLETED, FAILED
    filename: str
    created_at: str


# --- Endpoints ---

@router.post("/upload", response_model=UploadResponse)
async def create_upload_url(request: UploadRequest):
    """
    Step 1 of upload flow.
    Browser calls this to get a presigned URL.
    Browser then uploads the file directly to S3 using that URL.
    Our server never touches the file bytes.
    """
    # Generate a unique ID for this document
    document_id = str(uuid.uuid4())

    # The S3 key (path inside the bucket) for this file
    # e.g. "uploads/abc-123/invoice.pdf"
    s3_key = f"uploads/{document_id}/{request.filename}"

    # Generate the presigned URL
    # This URL lets anyone PUT a file to S3 for the next 3600 seconds
    s3_client = get_s3_client()
    upload_url = s3_client.generate_presigned_url(
        "put_object",                          # HTTP method S3 expects
        Params={
            "Bucket": settings.s3_bucket_name,
            "Key": s3_key,
            "ContentType": request.content_type,
        },
        ExpiresIn=3600,                        # URL valid for 1 hour
    )

    # Save document metadata to DynamoDB immediately
    # Status starts as PENDING — worker will update it
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(settings.dynamodb_table_name)

    table.put_item(Item={
        "PK": f"DOC#{document_id}",   # partition key
        "SK": "METADATA",              # sort key
        "document_id": document_id,
        "filename": request.filename,
        "s3_key": s3_key,
        "status": "PENDING",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "content_type": request.content_type,
    })

    return UploadResponse(
        document_id=document_id,
        upload_url=upload_url,
        expires_in=3600,
    )


@router.get("/{document_id}/status", response_model=DocumentStatus)
async def get_document_status(document_id: str):
    """
    Poll this endpoint to check if processing is done.
    Returns the current status: PENDING, PROCESSING, COMPLETED, FAILED
    """
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(settings.dynamodb_table_name)

    # Fetch the item from DynamoDB using PK + SK
    response = table.get_item(Key={
        "PK": f"DOC#{document_id}",
        "SK": "METADATA",
    })

    # If no item found, document doesn't exist
    item = response.get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="Document not found")

    return DocumentStatus(
        document_id=item["document_id"],
        status=item["status"],
        filename=item["filename"],
        created_at=item["created_at"],
    )