from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    aws_region: str = "eu-west-1"
    app_env: str = "development"
    log_level: str = "INFO"
    s3_bucket_name: str = "doc-intelligence-documents-dev"
    dynamodb_table_name: str = "doc-intelligence-documents-dev"
    bedrock_model_id: str = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
    sqs_queue_url: str = "https://sqs.eu-west-1.amazonaws.com/549116506173/doc-intelligence-documents-dev"
    bedrock_kb_id: str = "GTW9CRTHWL"
    bedrock_kb_data_source_id: str = "PPSRC9SMY5"


settings = Settings()