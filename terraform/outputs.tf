output "s3_bucket_name" {
  description = "Name of the documents S3 bucket"
  value       = aws_s3_bucket.documents.bucket
}

output "s3_bucket_arn" {
  description = "ARN of the documents S3 bucket"
  value       = aws_s3_bucket.documents.arn
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB table"
  value       = aws_dynamodb_table.documents.name
}

output "dynamodb_table_arn" {
  description = "ARN of the DynamoDB table"
  value       = aws_dynamodb_table.documents.arn
}
output "sqs_queue_url" {
  description = "URL of the document processing SQS queue"
  value       = aws_sqs_queue.documents.url
}

output "sqs_queue_arn" {
  description = "ARN of the document processing SQS queue"
  value       = aws_sqs_queue.documents.arn
}

output "knowledge_base_id" {
  description = "Bedrock Knowledge Base ID"
  value       = aws_bedrockagent_knowledge_base.documents.id
}

output "data_source_id" {
  description = "Bedrock Knowledge Base Data Source ID"
  value       = aws_bedrockagent_data_source.documents.data_source_id
}
output "ecr_repository_url" {
  description = "URL of the ECR repository (used to tag and push images)"
  value       = aws_ecr_repository.app.repository_url
}