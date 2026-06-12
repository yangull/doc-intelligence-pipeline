import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.config import settings
from app.core.aws_clients import get_s3_client, get_dynamodb_resource
from app.pipeline.query_graph import run_query_pipeline

router = APIRouter(prefix="/documents", tags=["documents"])

class UploadRequest(BaseModel):
    filename: str
    content_type: str

class UploadResponse(BaseModel):
    document_id: str
    upload_url: str
    expires_in: int

class DocumentStatus(BaseModel):
    document_id: str
    status: str
    filename: str
    created_at: str

class QueryRequest(BaseModel):
    question: str

class QuerySource(BaseModel):
    document_id: str
    excerpt: str

class QueryResponse(BaseModel):
    answer: str
    sources: list[QuerySource]

@router.post("/upload", response_model=UploadResponse)
async def create_upload_url(request: UploadRequest):
    document_id = str(uuid.uuid4())
    s3_key = f"uploads/{document_id}/{request.filename}"
    s3_client = get_s3_client()
    upload_url = s3_client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.s3_bucket_name,
            "Key": s3_key,
            "ContentType": request.content_type,
        },
        ExpiresIn=3600,
    )
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(settings.dynamodb_table_name)
    table.put_item(Item={
        "PK": f"DOC#{document_id}",
        "SK": "METADATA",
        "document_id": document_id,
        "filename": request.filename,
        "s3_key": s3_key,
        "status": "PENDING",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "content_type": request.content_type,
    })
    return UploadResponse(document_id=document_id, upload_url=upload_url, expires_in=3600)

@router.get("/{document_id}/status", response_model=DocumentStatus)
async def get_document_status(document_id: str):
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(settings.dynamodb_table_name)
    response = table.get_item(Key={"PK": f"DOC#{document_id}", "SK": "METADATA"})
    item = response.get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentStatus(
        document_id=item["document_id"],
        status=item["status"],
        filename=item["filename"],
        created_at=item["created_at"],
    )

@router.post("/query", response_model=QueryResponse)
async def query_documents(request: QueryRequest):
    result = run_query_pipeline(request.question)  # root trace wraps all nodes

    sources = []
    for i, chunk in enumerate(result["retrieved_chunks"]):
        uri = chunk.get("source", "")
        parts = uri.split("/")
        # S3 URI format: s3://bucket/uploads/{doc_id}/filename
        doc_id = parts[4] if len(parts) >= 5 else f"source-{i}"
        sources.append(QuerySource(
            document_id=doc_id,
            excerpt=chunk["text"][:300],
        ))

    return QueryResponse(answer=result["answer"], sources=sources)
