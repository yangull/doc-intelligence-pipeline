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
    sqs_queue_url: str
    bedrock_kb_id: str
    bedrock_kb_data_source_id: str

    api_key: str = ""
    cors_allow_origins: str = ""

    langfuse_secret_key: str = ""
    langfuse_public_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


settings = Settings()
