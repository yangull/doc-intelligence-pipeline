import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from app.core.config import settings
from app.core.aws_clients import get_s3_client, get_dynamodb_resource
from app.pipeline.query_graph import MalformedModelResponse, run_query_pipeline


def require_api_key(x_api_key: str = Header(default="")):
    # Auth is off when API_KEY is unset (local development)
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    dependencies=[Depends(require_api_key)],
)

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

# Handlers are plain `def` on purpose: boto3 and the query pipeline are
# blocking, so FastAPI runs these in a threadpool instead of stalling the
# event loop
@router.post("/upload", response_model=UploadResponse)
def create_upload_url(request: UploadRequest):
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
def get_document_status(document_id: str):
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
def query_documents(request: QueryRequest):
    try:
        result = run_query_pipeline(request.question)  # root trace wraps all nodes
    except MalformedModelResponse as exc:
        # The forced tool-use response carried no toolUse block, was truncated, or had
        # no answer. That is the model misbehaving, not a bad request — surface a clean
        # 502 instead of a 500 with a stack trace. Caught by exact type rather than
        # ValueError so an unrelated ValueError from boto3 or the graph still raises as
        # a genuine 500 instead of being mislabelled a model fault.
        raise HTTPException(
            status_code=502, detail="Model returned a malformed response"
        ) from exc

    # Only the chunks the answer actually cites, not everything retrieval returned.
    # generator() already dropped out-of-range and non-integer indices, so an
    # abstention yields no sources rather than five confident-looking ones.
    chunks = result["retrieved_chunks"]
    sources = []
    for index in result["cited_chunk_indices"]:
        chunk = chunks[index - 1]  # cited_chunk_indices is 1-based
        uri = chunk.get("source", "")
        parts = uri.split("/")
        # S3 URI format: s3://bucket/uploads/{doc_id}/filename
        doc_id = parts[4] if len(parts) >= 5 else f"source-{index - 1}"
        sources.append(QuerySource(
            document_id=doc_id,
            excerpt=chunk["text"][:300],
        ))

    return QueryResponse(answer=result["answer"], sources=sources)
