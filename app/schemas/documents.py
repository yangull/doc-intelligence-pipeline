import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.config import settings
from app.core.aws_clients import get_s3_client, get_dynamodb_resource, get_bedrock_agent_runtime_client

router = APIRouter(prefix="/documents", tags=["documents"])

# --- Request/Response models ---
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

# --- Endpoints ---
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

    return UploadResponse(
        document_id=document_id,
        upload_url=upload_url,
        expires_in=3600,
    )


@router.get("/{document_id}/status", response_model=DocumentStatus)
async def get_document_status(document_id: str):
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(settings.dynamodb_table_name)

    response = table.get_item(Key={
        "PK": f"DOC#{document_id}",
        "SK": "METADATA",
    })

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
    client = get_bedrock_agent_runtime_client()

    response = client.retrieve_and_generate(
        input={"text": request.question},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": settings.bedrock_kb_id,
                "modelArn": f"arn:aws:bedrock:eu-west-1::foundation-model/{settings.bedrock_model_id}",
            },
        },
    )

    answer = response["output"]["text"]

    sources = []
    citations = response.get("citations", [])
    for citation in citations:
        for reference in citation.get("retrievedReferences", []):
            s3_key = reference["location"]["s3Location"]["uri"]
            parts = s3_key.split("/")
            document_id = parts[2] if len(parts) >= 3 else "unknown"
            sources.append(QuerySource(
                document_id=document_id,
                excerpt=reference["content"]["text"][:300],
            ))

    return QueryResponse(answer=answer, sources=sources)