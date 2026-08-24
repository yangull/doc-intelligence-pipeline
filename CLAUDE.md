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

Status: corpus (7 documents), dataset (16 cases incl. 3 negatives), and the deterministic
metrics (retrieval hit rate, citation accuracy, answer match, cited-nothing, latency, cost)
are built. `generator()` now uses forced tool use so citation accuracy is distinct from
retrieval hit rate. A fresh baseline exists (2026-08-24, see below). Still to do:
faithfulness LLM-judge, CI gate, `EVALS.md`, dashboard.

**Pricing is resolved (2026-08-13).** `eval/pricing.json` holds **$3.30 in / $16.50 out per
1M tokens**. AWS publishes no rate for Sonnet 4.5 anywhere — not on the Bedrock pricing
page, and the Price List API has zero Anthropic SKUs for `eu-west-1` — so the figures were
derived from Cost Explorer under the service name `Claude Sonnet 4.5 (Amazon Bedrock
Edition)` (not `Amazon Bedrock`), filtered to `RECORD_TYPE=Usage` because account credits
net the charges to $0. Both divisions land on exact round numbers. **These are 1.10x the
Anthropic first-party rates ($3/$15)** — the EU marketplace uplift — so never substitute
first-party pricing. Full derivation is in the file's `source` field.

**Current baseline (2026-08-24), the first real one:** 16 cases over the 7-document corpus
(after dropping `contract-nda-mutual.pdf` — see Decision 4). Retrieval hit rate 100%,
citation accuracy 100%, answer match rate 100%, cited-nothing 100% (3 negatives), latency
p50 5.179s / p95 5.985s, 23,969 tokens in / 1,522 out, cost $0.1042. Both scanned-invoice
cases (`vogel-scan-total`, `vogel-scan-invoice-number`) pass end-to-end — retrieval and
citation, not just KB indexing — confirming the foundation-model parser fix genuinely
closes the loop. Results file: `eval/results/20260824T121954Z.json`.

The first-ever run (2026-08-12) scored 78.6% across the board on 17 cases over 8 documents;
its three misses were the cases whose documents never indexed (on comparable cases it was
11/11). That results file was **deleted** (recoverable via `git show`): it described a
corpus that no longer exists and used pre-rename keys. Record both runs as history in
`EVALS.md` when it's written; do not plot them on the same axis — the 2026-08-12 run and
this one measured different corpora under different parsing configs.

### Where things stand (as of 2026-08-24)

**The deployment gap is closed and verified** (2026-08-13). The worker ECS service,
SSM-backed API auth, and cited-only `/query` sources are applied and live. A PDF was
uploaded through the deployed API and reached `COMPLETED` in 20 seconds **with no worker
running locally**. Production upload works for the first time. Both services sit at
`desired_count = 0`; nothing is billing.

**The foundation-model-parsing resume plan is fully complete (2026-08-24).** `terraform
apply` ran (3 add, 3 change, 3 destroy, exactly as predicted — the data source was
replaced; hit one snag along the way, the KB service role needed `bedrock:GetInferenceProfile`
added alongside `bedrock:InvokeModel`, now in `main.tf`). New `BEDROCK_KB_DATA_SOURCE_ID`
is `FQ3FQ3SPLZ`, written to `.env` (`BEDROCK_KB_ID` unchanged: `GTW9CRTHWL`). `reset_corpus
--yes` purged S3/DynamoDB (10 objects, 22 items — the duplicate `invoice-nordwind-2401.pdf`
turned out to be **3** copies, not 1). Corpus re-ingested, `invoice-scanned-lowquality.pdf`
reached `INDEXED` (Decision 3, closed), `contract-nda-mutual.pdf` failed again and was
dropped (Decision 4, closed — see below). Fresh baseline run: **100% across every metric**,
see the baseline note above. Tests green (`tests/test_eval_corpus.py`, 20/20).

**Nothing left from the resume plan. Next real task is the "Still to do" list above** —
the faithfulness LLM-judge is the natural starting point (design it via `grill-with-docs`
first; it's genuinely undesigned, unlike the resume steps were).

**Housekeeping before the next session:** this session's changes are uncommitted —
`CLAUDE.md`, `terraform/main.tf`, the four NDA-drop files, and the new results file under
`eval/results/`. Commit before moving on to new work, so a fresh session doesn't inherit a
dirty working tree.

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

Decisions 1, 2, 5, and 6 settled 2026-08-13; decisions 3 and 4 settled 2026-08-24:

1. **Settled — fixed.** `/query` now builds `sources` from `cited_chunk_indices`, so an
   abstention returns `sources: []` instead of five confident-looking chunks.
2. **Settled — accepted permanently.** `trigger_kb_ingestion` sends no inline metadata;
   Bedrock rejects `IN_LINE_ATTRIBUTE` for S3-backed data sources, and the S3 key already
   carries the document id. No sidecar `.metadata.json` files.
3. **Settled — fixed and verified (2026-08-24).** Foundation-model parsing is configured on
   the data source, and `invoice-scanned-lowquality.pdf` reached `INDEXED` after the
   post-`terraform apply` re-ingest. Closed — no further verification needed.

   Note the pipeline was never fully blind to scans: extraction always worked, because the
   worker sends the PDF to Claude directly. Only Knowledge Base indexing failed, so a
   scanned invoice produced correct structured data in DynamoDB but could not be queried.
4. **Settled — drop it again (2026-08-24).** Re-tested under the new parser and it failed
   the same way: `statusReason` empty, no explanation. That rules out "the old default
   parser was the cause." The original mystery from 2026-08-13 (identical bytes under a
   fresh S3 key also failed) stands unexplained. Per the pre-agreed contingency, drop the
   document and its case — see the resume steps above. Worth a line in `EVALS.md` once
   that file exists: one content-specific indexing failure with no known cause.
5. **Settled — deleted.** The old baseline results file is gone; see the baseline note
   above. A `schema_version` field on harness output is still worth adding.

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
- **There are two independent "readers", and they have different capabilities.** The worker
  sends the PDF to Claude, which has vision, so extraction handles scanned documents fine.
  The Knowledge Base parses separately at ingestion; its default parser reads only a PDF's
  text layer. That is why a scan could extract perfectly and still be unsearchable. The
  data source now sets `parsing_configuration` to `BEDROCK_FOUNDATION_MODEL` so both use
  Claude. Adding it **forces the data source to be replaced**, which changes
  `BEDROCK_KB_DATA_SOURCE_ID`, wipes the index, and replaces both ECS task definitions —
  update `.env` from `terraform output data_source_id` and re-ingest afterwards. The parser
  applies to **every** PDF in the data source, not just scans, and is billed per token at
  ingestion (~$0.012/page).
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
