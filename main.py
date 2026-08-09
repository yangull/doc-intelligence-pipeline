from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.documents import router as documents_router
from app.core.config import settings
from app.core.logging import setup_logging

setup_logging()

# Create the FastAPI app
app = FastAPI(
    title="Document Intelligence Pipeline",
    description="Upload documents, extract structured data, query with natural language",
    version="0.1.0",
)

# CORS_ALLOW_ORIGINS is a comma-separated list of allowed frontend origins;
# unset means wildcard, which browsers only accept without credentials
cors_origins = settings.cors_origins or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register the documents router
# All endpoints in documents.py will be available under /documents/...
app.include_router(documents_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    """Simple health check endpoint — used by load balancers to verify the app is running."""
    return {
        "status": "healthy",
        "environment": settings.app_env,
        "version": "0.1.0",
    }