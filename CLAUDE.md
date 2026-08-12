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

Status: corpus, dataset (17 cases incl. 3 negatives), and the deterministic metrics
(retrieval hit rate, citation accuracy, answer match, cited-nothing, latency, cost) are
built. `generator()` now uses forced tool use so citation accuracy is distinct from
retrieval hit rate. `eval/pricing.json` is unfilled — the harness refuses to run
without real Bedrock rates rather than guessing (`--allow-missing-pricing` to skip
cost). Still to do: faithfulness LLM-judge, CI gate, `EVALS.md`, dashboard.

First recorded baseline (2026-08-12, `eval/results/20260812T195721Z.json`): retrieval
hit rate 78.6%, citation accuracy 78.6%, answer match 78.6%, cited-nothing 100% (3/3
negatives), latency p50 5.07s / p95 5.87s. The three misses are the three cases whose
documents failed to index, not quality failures — on the 11 indexed documents it scored
11/11. All three rates being equal is coincidence, not evidence that citation accuracy
is redundant; no case has yet separated them in a live run.

### Where things stand (end of session, 2026-08-13)

**Three commits exist locally and are deliberately NOT pushed** — `main` is ahead of
`origin/main` by 3. Working tree clean, 80 tests pass, ruff clean, pre-commit hooks
installed (ruff on commit, pytest on push).

```
9210dbc Add eval harness for the query pipeline
b29337d Return only the chunks the answer cites from the query pipeline
0e8fc9f Fix KB ingestion rejected for S3-backed data sources
```

**Pushing deploys** (`.github/workflows/deploy.yml` on push to `main`: ruff + pytest gate,
then ECR build and `ecs update-service`). The strongest reason to push soon is that
**document upload is broken in production until `0e8fc9f` lands** — every PDF failed at
the KB-ingestion step. ECS sits at `desired_count = 0`, so a deploy updates the service
definition and starts no task; there is no compute cost and the fix is not observable
until someone scales up to demo. Do not push without asking.

**The harness will refuse to run as-is.** `corpus_manifest.json` (repaired via
`--verify-only`) records the true KB state: 6 INDEXED, 2 FAILED —
`contract-nda-mutual.pdf` and `invoice-scanned-lowquality.pdf`. Resolve open decisions 3
and 4, or pass `--allow-unindexed` to measure anyway (affected cases are stamped
`expected_source_unindexed`).

All findings from three pre-commit review rounds are fixed. Hardening worth knowing about,
because each was a real defect rather than a style change: `/query` returns 502 on a
malformed model response; `cited_chunks` rejects booleans and out-of-range indices
(`bool` subclasses `int`, so `isinstance` accepted `True` as chunk 1); pricing rates must
be JSON numbers, checked before any Bedrock spend; errored harness cases score `None` on
every metric rather than as misses or — for negatives — as passes; `ingest_corpus` refuses
to run against a non-empty KB and writes its manifest incrementally; `tests/conftest.py`
blanks Langfuse credentials so pytest cannot ship traces to the real project (runs before
2026-08-12 did — see the cleanup note below).

### Open decisions (carried over — ask the user, do not decide unilaterally)

1. `/query` in `app/api/documents.py` still builds `sources` from all `retrieved_chunks`,
   ignoring the `citations` / `cited_chunk_indices` the pipeline now produces. It reports
   five "sources" even when the model cited nothing. Fixing it changes a public response
   shape.
2. `trigger_kb_ingestion` had its inline metadata removed to unbreak ingestion (Bedrock
   rejects `IN_LINE_ATTRIBUTE` metadata for an S3-backed data source). Accept permanently,
   or restore `document_id` later via sidecar `.metadata.json` files?
3. `invoice-scanned-lowquality.pdf` will not index: it is a genuine raster scan and the
   data source uses the default parser, which has no OCR. Enable a foundation-model parser
   (costs per page, belongs in `terraform/`) or drop the document and its two cases?
4. `contract-nda-mutual.pdf` fails to index for unknown reasons — empty `statusReason`, and
   identical bytes under a fresh S3 key fail too, so it is content-specific. Keep digging
   (CloudWatch, console, or regenerate with different text) or park it?
5. The baseline results file uses pre-rename keys (`abstention_rate`, `total_cost_usd`),
   and the schema has since diverged further (`errored_cases`, `error`,
   `expected_source_unindexed` fields added). Re-run for a clean baseline (~34 Bedrock
   calls; needs `--allow-unindexed` while decision 3/4 are open), drop it, or leave it
   with a note? Consider adding a `schema_version` field to harness output either way.

(Decision 6, commit strategy, is settled: split into the three commits listed above.)

### Only the user can do these

- Fill `eval/pricing.json` with real Bedrock rates for `eu-west-1`. The public pricing page
  lists no Sonnet 4.5 entry; Cost Explorer against today's recorded token counts is the most
  reliable source. Record `source` and `checked_on` so the number stays traceable.
- Anything that provisions or reconfigures AWS infrastructure (e.g. the parser in item 3).
- Earlier `pytest` runs shipped synthetic traces into the real Langfuse project before
  `tests/conftest.py` was fixed to blank the credentials; those traces may want deleting.

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
