# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Portfolio AWS document-intelligence app: FastAPI API + background worker that extracts
structured data from uploaded PDFs via Bedrock (forced tool use) and ingests them into a
Bedrock Knowledge Base. Repo: yangull/doc-intelligence-pipeline.

## Current focus: Phase 1 — LLM eval harness

Building a rigorous eval harness for the query pipeline (weeks 1–5 of the user's
self-improvement blueprint):

1. Manually curated ground-truth dataset at `eval/dataset.json` (question /
   expected answer / expected citation triples, growing to 50+).
2. Metrics: retrieval hit rate, answer faithfulness (LLM-as-judge with a written
   rubric), citation accuracy, cost + latency per query.
3. Plain pytest + hand-rolled harness first (learning goal); evaluate promptfoo
   or Braintrust later.
4. Wire into CI so prompt/model changes that regress quality fail the build.
5. Deliverables: `EVALS.md` (metric choices + known weaknesses) and a small
   quality-over-time dashboard.

No fabricated metrics anywhere — every number must be reproducible from the harness.

Status: corpus (6 documents), dataset (14 cases incl. 3 negatives), and the deterministic
metrics (retrieval hit rate, citation accuracy, answer match, cited-nothing, latency, cost)
are built. `generator()` now uses forced tool use so citation accuracy is distinct from
retrieval hit rate. Still to do: faithfulness LLM-judge, CI gate, `EVALS.md`, dashboard,
and a fresh baseline on the 6-document corpus.

**Pricing is resolved (2026-08-13).** `eval/pricing.json` holds **$3.30 in / $16.50 out per
1M tokens**. AWS publishes no rate for Sonnet 4.5 anywhere — not on the Bedrock pricing
page, and the Price List API has zero Anthropic SKUs for `eu-west-1` — so the figures were
derived from Cost Explorer under the service name `Claude Sonnet 4.5 (Amazon Bedrock
Edition)` (not `Amazon Bedrock`), filtered to `RECORD_TYPE=Usage` because account credits
net the charges to $0. Both divisions land on exact round numbers. **These are 1.10x the
Anthropic first-party rates ($3/$15)** — the EU marketplace uplift — so never substitute
first-party pricing. Full derivation is in the file's `source` field.

Superseded baseline (2026-08-12, `eval/results/20260812T195721Z.json`): retrieval hit rate
78.6%, citation accuracy 78.6%, answer match 78.6%, cited-nothing 100%, latency p50 5.07s /
p95 5.87s. Its three misses were the cases whose documents failed to index; those documents
and cases have since been dropped, so this file describes a corpus that no longer exists.
It also predates several schema renames. Treat it as history, not a comparison point.

### Where things stand (end of session, 2026-08-13)

**Four commits exist locally and are deliberately NOT pushed** — `main` is ahead of
`origin/main` by 4, plus a batch of uncommitted working-tree changes (see below). Pre-commit
hooks installed (ruff on commit, pytest on push).

```
c47ad85 Record session handoff in CLAUDE.md
9210dbc Add eval harness for the query pipeline
b29337d Return only the chunks the answer cites from the query pipeline
0e8fc9f Fix KB ingestion rejected for S3-backed data sources
```

**Pushing deploys** (`.github/workflows/deploy.yml` on push to `main`: ruff + pytest gate,
then ECR build and `ecs update-service` for both services). The strongest reason to push
soon is that **document upload is broken in production until `0e8fc9f` lands** — every PDF
failed at the KB-ingestion step. Both ECS services sit at `desired_count = 0`, so a deploy
updates service definitions and starts no task; there is no compute cost and the fix is not
observable until someone scales up to demo. Do not push without asking.

**The worker now has an ECS service, but it is not applied yet.** `terraform/main.tf`
defines `aws_ecs_service.worker` / `aws_ecs_task_definition.worker` / a matching egress-only
security group. Until `terraform apply` runs, AWS still has no SQS consumer, so an upload in
production stays `PENDING` forever unless a laptop runs `python -m app.worker.worker`.
Verified plan: **4 to add, 2 to change, 1 to destroy** — the destroy is
`aws_ecs_task_definition.app` being *replaced*, which is normal (task definitions are
immutable, so any edit registers a new revision). A destroy of the S3 bucket, DynamoDB
table, SQS queue, or Knowledge Base would be the real stop signal.

**The harness can now run clean.** `corpus_manifest.json` lists only the 6 INDEXED
documents, so no `--allow-unindexed` is needed. A run is 28 Bedrock calls, roughly $0.10.

All findings from three pre-commit review rounds are fixed. Hardening worth knowing about,
because each was a real defect rather than a style change: `/query` returns 502 on a
malformed model response; `cited_chunks` rejects booleans and out-of-range indices
(`bool` subclasses `int`, so `isinstance` accepted `True` as chunk 1); pricing rates must
be JSON numbers, checked before any Bedrock spend; errored harness cases score `None` on
every metric rather than as misses or — for negatives — as passes; `ingest_corpus` refuses
to run against a non-empty KB and writes its manifest incrementally; `tests/conftest.py`
blanks Langfuse credentials so pytest cannot ship traces to the real project (runs before
2026-08-12 did — see the cleanup note below).

### Open decisions

Decisions 1–4 and 6 are **settled** (2026-08-13):

1. **Settled — fixed.** `/query` now builds `sources` from `cited_chunk_indices`, so an
   abstention returns `sources: []` instead of five confident-looking chunks.
2. **Settled — accepted permanently.** `trigger_kb_ingestion` sends no inline metadata;
   Bedrock rejects `IN_LINE_ATTRIBUTE` for S3-backed data sources, and the S3 key already
   carries the document id. No sidecar `.metadata.json` files.
3. **Settled — dropped.** `invoice-scanned-lowquality.pdf` and its two cases are gone. No
   foundation-model parser was enabled, so the pipeline still has **no OCR path**: a
   scanned, image-only PDF will not index. Record this in `EVALS.md` as a known weakness.
4. **Settled — dropped.** `contract-nda-mutual.pdf` and its case are gone. The cause was
   never found (empty `statusReason`, identical bytes fail under a fresh S3 key), so this
   is an unexplained content-specific indexing failure, not a fixed bug. Also worth an
   `EVALS.md` note.

Still open:

5. The baseline results file uses pre-rename keys (`abstention_rate`, `total_cost_usd`) and
   the schema has diverged further (`errored_cases`, `error`, `expected_source_unindexed`).
   It also describes the old 8-document corpus. Re-run for a clean baseline (28 Bedrock
   calls, ~$0.10, no `--allow-unindexed` needed now), drop the old file, or keep it with a
   note? Consider adding a `schema_version` field to harness output either way.

(Decision 6, commit strategy, is settled: split into the commits listed above.)

### Only the user can do these

- ~~Fill `eval/pricing.json`~~ — **done 2026-08-13**, see the pricing note above.
- ~~Create the API-key SSM parameter~~ — **done**, `/doc-intelligence/dev/api-key`,
  SecureString, Version 1, `eu-west-1`.
- `terraform apply` and anything else that provisions or reconfigures AWS infrastructure.
- Scaling either ECS service above `desired_count = 0` (~$0.05/hour each).
- Pushing to `main`, which auto-deploys.
- Earlier `pytest` runs shipped synthetic traces into the real Langfuse project before
  `tests/conftest.py` was fixed to blank the credentials; those traces may want deleting.

Note the `canoa` IAM user **can** query Cost Explorer via the CLI (`aws ce
get-cost-and-usage`) even though the console's Cost widget returns "Access denied" — the
console widget needs a separate billing permission the API does not.

## Commands

```bash
uv sync                          # install deps
uv run pytest                    # run tests (must pass before any push)
uv run pytest tests/test_worker.py -k test_name   # run a single test
uv run ruff check .              # lint (line-length 100)
uv run pytest --cov=app --cov=main   # tests with coverage report
uv run pre-commit install --hook-type pre-commit --hook-type pre-push  # one-time hook setup after clone
uv run uvicorn main:app --reload # run API locally
uv run python -m app.worker.worker  # run worker locally
```

Eval harness (all scripts are `python -m`, since `eval/` is a package):

```bash
uv run python -m eval.generate_corpus   # regenerate eval/corpus/*.pdf (deterministic)
uv run python -m eval.reset_corpus      # dry run: what would be purged from KB/S3/DynamoDB
uv run python -m eval.reset_corpus --yes  # DESTRUCTIVE purge
uv run python -m eval.ingest_corpus     # upload corpus via the running API; writes corpus_manifest.json
uv run python -m eval.ingest_corpus --verify-only  # re-check kb_status only (free, no Bedrock)
uv run python -m eval.harness           # score the dataset; writes eval/results/{timestamp}.json
```

The manifest tracks two statuses per document: `status` (app-reported; COMPLETED only means
the ingest call was accepted) and `kb_status` (the Knowledge Base's own view; only INDEXED
counts). The harness gates on `kb_status` — `--allow-unindexed` overrides, stamping
affected results `expected_source_unindexed`. Errored harness cases score None on every
metric (excluded from rates and latency percentiles, counted in `errored_cases`, non-zero
exit).

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
  job (ruff + pytest) gates a build/push to ECR + a forced redeploy of **both** ECS
  services via OIDC.
- **Two ECS Fargate services**, both at **`desired_count = 0`** on purpose to keep costs
  near-free: `doc-intelligence-api-dev` (uvicorn) and `doc-intelligence-worker-dev`
  (`python -m app.worker.worker`). Same image; the worker's task definition overrides the
  command. Do not raise either without asking. Demo pattern: set `desired_count = 1` on
  **both**, apply, curl the API task's public IP on port 8080, scale back to 0 (no ALB by
  design — see README ADR-3). A demo with only the API up leaves uploads stuck at
  `PENDING`, because nothing drains the queue.
- The API key comes from SSM (`/doc-intelligence/dev/api-key`), injected as a container
  secret at launch — there is no `api_key` Terraform variable any more.
- This project is **budget-sensitive**: prefer changes that reduce AWS cost (log volume,
  Bedrock calls, S3 scans). Never add always-on resources.

## Gotchas

- API auth is `X-API-Key`; auth is **disabled when the `API_KEY` env var is empty** (local dev).
- CORS origins come from `CORS_ALLOW_ORIGINS` (comma-separated; empty = wildcard).
- KB ingestion uses the `IngestKnowledgeBaseDocuments` API directly — no sidecar
  `metadata.json` files in S3, and no full data-source rescan.
- **The `IngestKnowledgeBaseDocuments` API authorizes against the IAM action
  `bedrock:StartIngestionJob`, not against an action of the same name.**
  `bedrock:IngestKnowledgeBaseDocuments` is not a real IAM action, so a policy listing
  only that grants nothing and every upload dies at the ingestion step with
  `AccessDeniedException`. Both are listed in `aws_iam_role_policy.apprunner_instance`;
  do not "tidy up" the seemingly redundant pair. This is invisible locally — local runs
  use a broad IAM user, while the ECS task role is scoped to exact ARNs, so only a real
  deployed upload reproduces it (found 2026-08-13, fixed in `9640936`).
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
- Git hooks via pre-commit: ruff runs on every commit, pytest on every push (matters
  because push to main deploys).

## Working with the user

- Backend/AI engineer, job hunting in Berlin; this repo is the flagship CV project.
- Explain changes in simple terms; one concrete action at a time, no menus of
  alternatives mid-step.
- Short answers, compact change lists; they read every diff.
- Use plan mode for anything non-trivial.
- Challenge their decisions directly on disagreement.
- Ask before commit/push and before anything that costs money in AWS.
