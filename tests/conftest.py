import os

os.environ.setdefault("SQS_QUEUE_URL", "https://sqs.eu-west-1.amazonaws.com/000000000000/test-queue")
os.environ.setdefault("BEDROCK_KB_ID", "TESTKB0000")
os.environ.setdefault("BEDROCK_KB_DATA_SOURCE_ID", "TESTDS0000")
os.environ.setdefault("S3_BUCKET_NAME", "test-bucket")
os.environ.setdefault("DYNAMODB_TABLE_NAME", "test-table")
