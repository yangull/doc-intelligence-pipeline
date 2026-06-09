# ============================================
# S3 BUCKET - stores uploaded documents
# ============================================
resource "aws_s3_bucket" "documents" {
  # Bucket names must be globally unique across all AWS accounts
  # so we add a random suffix later via random_id
  bucket = "${var.project_name}-documents-${var.environment}"
}

# Block all public access - documents are private
resource "aws_s3_bucket_public_access_block" "documents" {
  bucket = aws_s3_bucket.documents.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Enable versioning - keeps old versions of documents
resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

# ============================================
# DYNAMODB TABLE - stores extraction results
# ============================================
resource "aws_dynamodb_table" "documents" {
  name         = "${var.project_name}-documents-${var.environment}"
  billing_mode = "PAY_PER_REQUEST" # no capacity planning needed, pay per use
  hash_key     = "PK" # partition key
  range_key    = "SK" # sort key

  # Every item in DynamoDB must have PK and SK
  # We'll use patterns like:
  # PK = "DOC#<id>"  SK = "METADATA"  -> document info
  # PK = "DOC#<id>"  SK = "EXTRACTION" -> Claude's extracted data
  attribute {
    name = "PK"
    type = "S" # S = String
  }

  attribute {
    name = "SK"
    type = "S"
  }

  # Global Secondary Index - lets us query by status
  # e.g. "give me all documents with status=PROCESSING"
  global_secondary_index {
    name            = "StatusIndex"
    hash_key        = "SK"
    range_key       = "PK"
    projection_type = "ALL"
  }

  # Delete table when terraform destroy is run
  deletion_protection_enabled = false
}
# ============================================
# SQS QUEUE - buffers document processing jobs
# ============================================

# Dead Letter Queue - catches jobs that fail repeatedly
# If a job fails 3 times, it moves here instead of being lost
resource "aws_sqs_queue" "documents_dlq" {
  name                      = "${var.project_name}-documents-dlq-${var.environment}"
  message_retention_seconds = 1209600 # keep failed messages for 14 days
}

# Main processing queue
resource "aws_sqs_queue" "documents" {
  name                       = "${var.project_name}-documents-${var.environment}"
  visibility_timeout_seconds = 300 # worker has 5 minutes to process each job
  message_retention_seconds  = 86400 # keep unprocessed messages for 1 day

  # If a job fails 3 times, send it to the DLQ
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.documents_dlq.arn
    maxReceiveCount     = 3
  })
}

# Allow EventBridge to send messages to SQS
resource "aws_sqs_queue_policy" "documents" {
  queue_url = aws_sqs_queue.documents.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.documents.arn
      }
    ]
  })
}

# ============================================
# EVENTBRIDGE RULE - watches S3 for new files
# ============================================

# Enable S3 to send events to EventBridge
resource "aws_s3_bucket_notification" "documents" {
  bucket      = aws_s3_bucket.documents.id
  eventbridge = true # send all S3 events to EventBridge
}

# Rule: when a PDF lands in the uploads/ folder, route to SQS
resource "aws_cloudwatch_event_rule" "new_document" {
  name        = "${var.project_name}-new-document-${var.environment}"
  description = "Fires when a new document is uploaded to S3"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.documents.bucket] }
      object = { key = [{ prefix = "uploads/" }] }
    }
  })
}

# Connect the rule to SQS - send matching events to our queue
resource "aws_cloudwatch_event_target" "new_document_sqs" {
  rule      = aws_cloudwatch_event_rule.new_document.name
  target_id = "SendToSQS"
  arn       = aws_sqs_queue.documents.arn
}