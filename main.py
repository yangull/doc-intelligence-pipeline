from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.documents import router as documents_router
from app.core.config import settings

# Create the FastAPI app
app = FastAPI(
    title="Document Intelligence Pipeline",
    description="Upload documents, extract structured data, query with natural language",
    version="0.1.0",
)

# CORS middleware — allows browsers from any origin to call this API
# In production you'd restrict this to your frontend's domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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