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