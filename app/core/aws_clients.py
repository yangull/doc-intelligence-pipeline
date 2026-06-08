import boto3
from functools import lru_cache
from app.core.config import settings


# lru_cache means "create this once, reuse forever"
# Without it, every function call would create a new AWS connection
@lru_cache(maxsize=1)
def get_s3_client():
    """Returns a reusable S3 client."""
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
    )


@lru_cache(maxsize=1)
def get_dynamodb_client():
    """Returns a reusable DynamoDB client."""
    return boto3.client(
        "dynamodb",
        region_name=settings.aws_region,
    )


@lru_cache(maxsize=1)
def get_bedrock_client():
    """Returns a reusable Bedrock runtime client.
    
    bedrock-runtime is the client for CALLING models (inference).
    bedrock (without -runtime) is for managing models — we don't need that.
    """
    return boto3.client(
        "bedrock-runtime",
        region_name=settings.aws_region,
    )


@lru_cache(maxsize=1)
def get_dynamodb_resource():
    """Returns a DynamoDB resource (higher-level than client).
    
    boto3 has two interfaces:
    - client: low-level, 1:1 with AWS API calls
    - resource: higher-level, more Pythonic (e.g. table.put_item)
    We use resource for DynamoDB writes/reads, client for everything else.
    """
    return boto3.resource(
        "dynamodb",
        region_name=settings.aws_region,
    )