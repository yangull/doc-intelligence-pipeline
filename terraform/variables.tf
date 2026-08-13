variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "eu-west-1"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "doc-intelligence"
}
variable "cors_allow_origins" {
  description = "Comma-separated list of allowed CORS origins; empty means wildcard"
  type        = string
  default     = ""
}
