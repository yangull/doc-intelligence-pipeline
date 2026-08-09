# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Portfolio AWS document-intelligence app: FastAPI API + background worker that extracts
structured data from uploaded PDFs via Bedrock (forced tool use) and ingests them into a
Bedrock Knowledge Base. Repo: yangull/doc-intelligence-pipeline.

## Commands

```bash
uv sync                          # install deps
uv run pytest                    # run tests (must pass before any push)
uv run pytest tests/test_worker.py -k test_name   # run a single test
uv run ruff check .              # lint (line-length 100)
uv run uvicorn main:app --reload # run API locally
uv run python -m app.worker.worker  # run worker locally
```

Terraform lives in `terraform/`; run plan/apply from that directory.

## Architecture

Two entry points sharing `app/core/` (config, boto3 client factories, logging):

- **API** (`main.py` → `app/api/documents.py`): `/api/v1/documents/upload` returns a
  presigned S3 PUT URL and writes a PENDING record to DynamoDB; `/status` polls it;
  `/query` runs the RAG pipeline. Handlers are plain `def` on purpose — boto3 is
  blocking, so FastAPI runs them in a threadpool. Request/response Pydantic models
  live inline in `documents.py` (there is no `app/schemas/` package anymore).
- **Worker** (`app/worker/worker.py`): long-polls SQS. Message chain is
  S3 upload → EventBridge → SQS, so the body is an EventBridge event wrapping an S3
  event; S3 key format `uploads/{document_id}/{filename}` carries the identity.
  `extractor.py` downloads the PDF, calls Bedrock Converse with **forced tool use**
  (`record_extraction` tool → schema-validated JSON, no markdown stripping), saves to
  DynamoDB, then ingests the file into the Knowledge Base.
- **Query pipeline** (`app/pipeline/query_graph.py`): LangGraph `StateGraph` with three
  nodes — `query_rewriter → retriever → generator` — each `@observe()`-traced to
  Langfuse under a root `query_pipeline` trace.

DynamoDB is single-table: `PK=DOC#{id}`, `SK=METADATA` (status record) or `EXTRACTION`
(extracted fields + token usage). Status lifecycle: PENDING → PROCESSING → COMPLETED/FAILED.

### Config

`app/core/config.py` uses pydantic-settings (`.env` file). `SQS_QUEUE_URL`,
`BEDROCK_KB_ID`, and `BEDROCK_KB_DATA_SOURCE_ID` have no defaults — the app refuses to
start without them. `tests/conftest.py` sets fake values so tests never need real AWS.

## Deploy and cost — read before pushing

- **Pushing to `main` triggers the CI deploy** (`.github/workflows/deploy.yml`): a test
  job (ruff + pytest) gates a build/push to ECR + ECS redeploy via OIDC.
- Runs on ECS Fargate with **`desired_count = 0`** on purpose — the service is scaled to
  zero to keep costs near-free. Do not raise it without asking. Demo pattern: set
  `desired_count = 1` in `terraform/main.tf`, apply, curl the task's public IP on
  port 8080, scale back to 0 (no ALB by design — see README ADR-3).
- This project is **budget-sensitive**: prefer changes that reduce AWS cost (log volume,
  Bedrock calls, S3 scans). Never add always-on resources.

## Gotchas

- API auth is `X-API-Key`; auth is **disabled when the `API_KEY` env var is empty** (local dev).
- CORS origins come from `CORS_ALLOW_ORIGINS` (comma-separated; empty = wildcard).
- KB ingestion uses the `IngestKnowledgeBaseDocuments` API directly — no sidecar
  `metadata.json` files in S3, and no full data-source rescan.
- Worker classifies failures as transient (return `"retry"`, message redelivered up to
  3 times then DLQ) vs permanent (return `"failed"`, message deleted); keep that
  distinction when touching `app/worker/`. `is_transient_error` in `extractor.py` is
  the single decision point.
- Bedrock Converse rejects document names containing hyphens — `extract_document_with_claude`
  normalizes filenames before sending.
- In `query_graph.py`, Langfuse env vars must be set **before** `import langfuse`
  (it initializes on import) — don't reorder those imports.
- DynamoDB rejects floats; extraction results are round-tripped through
  `json.loads(..., parse_float=Decimal)` before `put_item`.
- `terraform.tfstate` is untracked on purpose.

## Working with the user

- Junior dev: explain changes in simple terms, one concept at a time.
- Ask before commit/push and before anything that costs money in AWS.
