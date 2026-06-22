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

# ============================================
# IAM ROLE - lets Bedrock access our resources
# ============================================

data "aws_caller_identity" "current" {}

resource "aws_iam_role" "bedrock_kb" {
  name = "${var.project_name}-bedrock-kb-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_kb" {
  name = "${var.project_name}-bedrock-kb-policy-${var.environment}"
  role = aws_iam_role.bedrock_kb.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3ReadDocuments"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.documents.arn,
          "${aws_s3_bucket.documents.arn}/*"
        ]
      },
      {
        Sid    = "UseEmbeddingModel"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel"]
        Resource = [
          "arn:aws:bedrock:eu-west-1::foundation-model/amazon.titan-embed-text-v2:0"
        ]
      },
      {
        Sid    = "S3VectorsReadWrite"
        Effect = "Allow"
        Action = [
          "s3vectors:CreateIndex",
          "s3vectors:DeleteIndex",
          "s3vectors:GetIndex",
          "s3vectors:ListIndexes",
          "s3vectors:PutVectors",
          "s3vectors:GetVectors",
          "s3vectors:DeleteVectors",
          "s3vectors:QueryVectors",
          "s3vectors:ListVectors"
        ]
        Resource = "*"
      }
    ]
  })
}

# ============================================
# S3 VECTORS - stores our document embeddings
# ============================================

# The vector bucket is the container — like an S3 bucket but for embeddings
resource "aws_s3vectors_vector_bucket" "kb" {
  vector_bucket_name = "${var.project_name}-vectors-${var.environment}"
}

# The index is the searchable collection inside the bucket.
# dimension=1024 must match Titan Embed Text v2 exactly.
# cosine distance measures similarity between vectors.
resource "aws_s3vectors_index" "kb" {
  vector_bucket_name = aws_s3vectors_vector_bucket.kb.vector_bucket_name
  index_name         = "doc-intelligence-index-dev"
  data_type          = "float32"
  dimension          = 1024
  distance_metric    = "cosine"
}

# ============================================
# BEDROCK KNOWLEDGE BASE
# ============================================

resource "aws_bedrockagent_knowledge_base" "documents" {
  name     = "${var.project_name}-kb-${var.environment}"
  role_arn = aws_iam_role.bedrock_kb.arn

  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = "arn:aws:bedrock:eu-west-1::foundation-model/amazon.titan-embed-text-v2:0"
    }
  }

  storage_configuration {
    type = "S3_VECTORS"
    s3_vectors_configuration {
      vector_bucket_arn = "arn:aws:s3vectors:eu-west-1:${data.aws_caller_identity.current.account_id}:bucket/${aws_s3vectors_vector_bucket.kb.vector_bucket_name}"
      index_name        = "doc-intelligence-index-dev"
    }
  }

  depends_on = [aws_iam_role_policy.bedrock_kb, aws_s3vectors_index.kb]
}

resource "aws_bedrockagent_data_source" "documents" {
  knowledge_base_id = aws_bedrockagent_knowledge_base.documents.id
  name              = "${var.project_name}-datasource-${var.environment}"

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn         = aws_s3_bucket.documents.arn
      inclusion_prefixes = ["uploads/"]
    }
  }

  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = "FIXED_SIZE"
      fixed_size_chunking_configuration {
        max_tokens         = 512
        overlap_percentage = 20
      }
    }
  }
}

# ============================================
# ECR REPOSITORY - private registry for our Docker images
# ============================================
resource "aws_ecr_repository" "app" {
  name                 = "${var.project_name}-${var.environment}"
  image_tag_mutability = "MUTABLE" # allow overwriting tags like :latest

  # ECR scans each pushed image for known vulnerabilities (free, good practice)
  image_scanning_configuration {
    scan_on_push = true
  }

  # Let `terraform destroy` remove the repo even if it still has images in it
  force_delete = true
}

# ============================================
# IAM ROLE - ECR pull access (kept from earlier; reusable)
# ============================================
resource "aws_iam_role" "apprunner_access" {
  name = "${var.project_name}-apprunner-access-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "build.apprunner.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "apprunner_access_ecr" {
  role       = aws_iam_role.apprunner_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

# ============================================
# IAM ROLE - the RUNNING app's identity (reused by Fargate task)
# Grants the app its AWS permissions: S3, DynamoDB, SQS, Bedrock, SSM.
# ============================================
resource "aws_iam_role" "apprunner_instance" {
  name = "${var.project_name}-apprunner-instance-${var.environment}"

  # Trust policy: ECS tasks assume this role at runtime
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Inline policy granting exactly the permissions the app needs (least privilege)
resource "aws_iam_role_policy" "apprunner_instance" {
  name = "${var.project_name}-apprunner-instance-policy-${var.environment}"
  role = aws_iam_role.apprunner_instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # S3: read uploaded PDFs and write back if needed
        Sid    = "S3Access"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.documents.arn,
          "${aws_s3_bucket.documents.arn}/*"
        ]
      },
      {
        # DynamoDB: read/write document records + query the StatusIndex GSI
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
          "dynamodb:Query", "dynamodb:DeleteItem"
        ]
        Resource = [
          aws_dynamodb_table.documents.arn,
          "${aws_dynamodb_table.documents.arn}/index/*"
        ]
      },
      {
        # SQS: the worker receives and deletes messages from the queue
        Sid    = "SQSAccess"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"
        ]
        Resource = [aws_sqs_queue.documents.arn]
      },
      {
        # Bedrock: invoke Claude for extraction + call the Knowledge Base for RAG
        Sid    = "BedrockAccess"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:Retrieve",
          "bedrock:RetrieveAndGenerate",
          "bedrock:StartIngestionJob"
        ]
        Resource = ["*"] # Bedrock model/KB ARNs vary; scoped by action set
      },
      {
        # SSM: read the two encrypted Langfuse parameters at startup
        Sid    = "SSMAccess"
        Effect = "Allow"
        Action = ["ssm:GetParameters", "ssm:GetParameter"]
        Resource = [
          "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/doc-intelligence/*"
        ]
      }
    ]
  })
}

# Reference the SSM parameters we created via the CLI (read-only).
# Terraform looks up their ARNs but does not manage their values.
data "aws_ssm_parameter" "langfuse_public_key" {
  name = "/doc-intelligence/langfuse-public-key"
}

data "aws_ssm_parameter" "langfuse_secret_key" {
  name = "/doc-intelligence/langfuse-secret-key"
}
# ============================================
# FARGATE NETWORKING - look up the default VPC + subnets
# ============================================
# Find the default VPC (we confirmed it exists) without hardcoding its ID
data "aws_vpc" "default" {
  default = true
}

# Get all subnets in that VPC — Fargate will place the task in these
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# ============================================
# ECS CLUSTER - the namespace that holds our service
# ============================================
resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-${var.environment}"
}

# ============================================
# CLOUDWATCH LOGS - where the container writes its logs
# ============================================
resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.project_name}-${var.environment}"
  retention_in_days = 7 # keep a week of logs; cheap
}

# ============================================
# SECURITY GROUP - firewall for the Fargate task
# ============================================
resource "aws_security_group" "app" {
  name        = "${var.project_name}-app-${var.environment}"
  description = "Allow inbound 8080 to the app, all outbound"
  vpc_id      = data.aws_vpc.default.id

  # Inbound: allow port 8080 from anywhere (so we can hit the public IP)
  ingress {
    description = "App HTTP port"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Outbound: allow everything (app needs to reach S3, DynamoDB, Bedrock, etc.)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1" # all protocols
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ============================================
# IAM ROLE - ECS task EXECUTION role
# Used by ECS itself (not our code) to pull the image and fetch secrets.
# ============================================
resource "aws_iam_role" "ecs_execution" {
  name = "${var.project_name}-ecs-execution-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# AWS-managed policy: grants ECR pull + CloudWatch Logs write
resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Extra inline policy: let ECS read the two SSM secrets at launch
# (the managed policy above does NOT include SSM access)
resource "aws_iam_role_policy" "ecs_execution_ssm" {
  name = "${var.project_name}-ecs-execution-ssm-${var.environment}"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["ssm:GetParameters"]
      Resource = [
        data.aws_ssm_parameter.langfuse_public_key.arn,
        data.aws_ssm_parameter.langfuse_secret_key.arn
      ]
    }]
  })
}
# ============================================
# ECS TASK DEFINITION - the blueprint for running our container
# ============================================
resource "aws_ecs_task_definition" "app" {
  family                   = "${var.project_name}-${var.environment}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc" # required for Fargate
  cpu                      = "1024"   # 1 vCPU
  memory                   = "2048"   # 2 GB

  # execution_role: ECS uses this to pull the image + fetch secrets at launch
  execution_role_arn = aws_iam_role.ecs_execution.arn
  # task_role: our running app uses this for S3/DynamoDB/SQS/Bedrock/SSM calls
  task_role_arn = aws_iam_role.apprunner_instance.arn

  # The container spec is a JSON array; one container here.
  container_definitions = jsonencode([
    {
      name      = "api"
      image     = "${aws_ecr_repository.app.repository_url}:latest"
      essential = true

      # Map the container's port 8080 to the host (awsvpc gives the task its own ENI)
      portMappings = [
        {
          containerPort = 8080
          protocol      = "tcp"
        }
      ]

      # Non-secret env vars
      environment = [
        { name = "APP_ENV", value = "production" }
      ]

      # Secrets: ECS fetches these from SSM at launch and injects as env vars.
      # "valueFrom" is the SSM parameter ARN, not the value itself.
      secrets = [
        {
          name      = "LANGFUSE_PUBLIC_KEY"
          valueFrom = data.aws_ssm_parameter.langfuse_public_key.arn
        },
        {
          name      = "LANGFUSE_SECRET_KEY"
          valueFrom = data.aws_ssm_parameter.langfuse_secret_key.arn
        }
      ]

      # Send container logs to the CloudWatch log group we created
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])
}

# ============================================
# ECS SERVICE - keeps one task running, gives it a public IP
# ============================================
resource "aws_ecs_service" "app" {
  name            = "${var.project_name}-api-${var.environment}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = 0 # run exactly one copy; set to 0 to scale down / save money
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true # no ALB — the task itself gets a public IP
  }
}
# ============================================
# GITHUB ACTIONS OIDC - lets CI deploy without stored AWS keys
# ============================================

# Register GitHub as a trusted OIDC identity provider.
# AWS will accept short-lived tokens issued by GitHub Actions.
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  # GitHub's OIDC thumbprint; AWS now validates via its trust store,
  # but the provider still requires this field.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

# The role GitHub Actions assumes to deploy. Trusted ONLY by our repo.
resource "aws_iam_role" "github_deploy" {
  name = "${var.project_name}-github-deploy-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        # Token audience must be AWS STS
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        # Token MUST come from our specific repo (any branch). This is the
        # critical lock: no other GitHub repo can assume this role.
        StringLike = {
          "token.actions.githubusercontent.com:sub" = "repo:yangull/doc-intelligence-pipeline:*"
        }
      }
    }]
  })
}

# Permissions for the deploy role: push images to ECR + update ECS.
resource "aws_iam_role_policy" "github_deploy" {
  name = "${var.project_name}-github-deploy-policy-${var.environment}"
  role = aws_iam_role.github_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Get an ECR auth token (needed before any push)
        Sid      = "ECRAuth"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*" # this specific action doesn't support resource scoping
      },
      {
        # Push/pull image layers to OUR repo only
        Sid    = "ECRPushPull"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload"
        ]
        Resource = aws_ecr_repository.app.arn
      },
      {
        # Trigger a new ECS deployment + read service/task state
        Sid    = "ECSDeploy"
        Effect = "Allow"
        Action = [
          "ecs:UpdateService",
          "ecs:DescribeServices",
          "ecs:DescribeTaskDefinition",
          "ecs:RegisterTaskDefinition"
        ]
        Resource = "*" # ECS deploy actions are awkward to scope; fine for portfolio
      },
      {
        # Allow passing the task + execution roles to ECS during deploy
        Sid    = "PassRoles"
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          aws_iam_role.ecs_execution.arn,
          aws_iam_role.apprunner_instance.arn
        ]
      }
    ]
  })
}