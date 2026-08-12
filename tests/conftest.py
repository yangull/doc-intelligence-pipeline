import os

os.environ.setdefault("SQS_QUEUE_URL", "https://sqs.eu-west-1.amazonaws.com/000000000000/test-queue")
os.environ.setdefault("BEDROCK_KB_ID", "TESTKB0000")
os.environ.setdefault("BEDROCK_KB_DATA_SOURCE_ID", "TESTDS0000")
os.environ.setdefault("S3_BUCKET_NAME", "test-bucket")
os.environ.setdefault("DYNAMODB_TABLE_NAME", "test-table")

# Forced, not setdefault: query_graph copies these out of .env into os.environ
# before importing langfuse, so a developer with real credentials would otherwise
# ship synthetic test traces into the same Langfuse project the eval reads from.
# The pre-push hook runs pytest, so that would happen on every push.
os.environ["LANGFUSE_SECRET_KEY"] = ""
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_HOST"] = "http://localhost:1"
os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
