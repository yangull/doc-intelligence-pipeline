"""Upload the eval corpus through the real API and record where each file landed.

Deliberately drives the running app rather than writing to S3 directly, so
ingestion exercises the same path production does: presigned PUT -> EventBridge
-> SQS -> worker -> Bedrock extraction -> KB ingestion.

Needs both processes running:
    uv run uvicorn main:app --reload
    uv run python -m app.worker.worker

Then:
    uv run python -m eval.ingest_corpus

Writes eval/corpus_manifest.json, which maps each filename to the single
canonical S3 URI it now occupies. The dataset resolves expected citations
through that file, so it survives re-ingestion under new document IDs.

Each entry carries two statuses on purpose:
  status     what the app reported (COMPLETED means the ingest *call* was
             accepted — indexing is asynchronous and can still fail after this)
  kb_status  what the Knowledge Base actually says; only INDEXED counts, and
             the harness gates on this field, not on `status`

    uv run python -m eval.ingest_corpus --verify-only

re-queries the Knowledge Base for the documents already in the manifest and
rewrites `kb_status` without uploading anything (list calls only, no Bedrock
spend) — the recovery path after a partial run or an out-of-band retry.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from botocore.exceptions import ClientError

from app.core.aws_clients import get_s3_client
from app.core.config import settings
from eval.reset_corpus import list_kb_documents

CORPUS_DIR = Path(__file__).parent / "corpus"
MANIFEST_PATH = Path(__file__).parent / "corpus_manifest.json"

API_BASE = os.environ.get("EVAL_API_BASE", "http://127.0.0.1:8000")
POLL_TIMEOUT_SECONDS = 300
POLL_INTERVAL_SECONDS = 3

# KB document statuses that mean "still working" rather than a terminal outcome
IN_FLIGHT_KB_STATUSES = {"STARTING", "IN_PROGRESS", "PENDING"}
KB_SETTLE_TIMEOUT_SECONDS = 300
KB_SETTLE_POLL_SECONDS = 5


def write_manifest(manifest: dict):
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")


def stamp_kb_statuses(manifest: dict, wait: bool) -> list[str]:
    """Record each document's real Knowledge Base status and persist the manifest.

    With wait=True, polls until no manifest document is still in flight (or the
    deadline passes) — freshly ingested documents list as STARTING/IN_PROGRESS
    for a while and may briefly not appear at all. With wait=False, takes a
    single snapshot of current reality (the --verify-only path).

    Returns the filenames whose kb_status is anything other than INDEXED.
    """
    deadline = time.monotonic() + (KB_SETTLE_TIMEOUT_SECONDS if wait else 0)
    while True:
        statuses = list_kb_documents()
        pending = [
            name
            for name, entry in manifest.items()
            if statuses.get(entry["s3_uri"]) in IN_FLIGHT_KB_STATUSES
            or (wait and entry["s3_uri"] not in statuses)
        ]
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(KB_SETTLE_POLL_SECONDS)

    for entry in manifest.values():
        entry["kb_status"] = statuses.get(entry["s3_uri"], "NOT_FOUND")
    write_manifest(manifest)
    return [name for name, entry in manifest.items() if entry["kb_status"] != "INDEXED"]


def auth_headers():
    return {"X-API-Key": settings.api_key} if settings.api_key else {}


def upload(client: httpx.Client, path: Path) -> str:
    response = client.post(
        f"{API_BASE}/api/v1/documents/upload",
        json={"filename": path.name, "content_type": "application/pdf"},
        headers=auth_headers(),
    )
    response.raise_for_status()
    body = response.json()

    put = client.put(
        body["upload_url"],
        content=path.read_bytes(),
        headers={"Content-Type": "application/pdf"},
    )
    put.raise_for_status()
    return body["document_id"]


def verify_key_exists(s3_key: str):
    # The key layout is owned by create_upload_url in app/api/documents.py; this
    # module only mirrors it. If that format ever changes, every expected citation
    # URI would stop matching and the whole dataset would score as a retrieval miss
    # with nothing failing. Fail here instead.
    try:
        get_s3_client().head_object(Bucket=settings.s3_bucket_name, Key=s3_key)
    except ClientError as exc:
        raise SystemExit(
            f"Expected the upload at s3://{settings.s3_bucket_name}/{s3_key}, but it is "
            f"not there ({exc.response.get('Error', {}).get('Code')}).\n"
            "The S3 key format in app/api/documents.py has probably changed; "
            "eval/ingest_corpus.py must be updated to match."
        ) from exc


def wait_for_status(client: httpx.Client, document_id: str) -> str:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    last = "PENDING"
    while time.monotonic() < deadline:
        response = client.get(
            f"{API_BASE}/api/v1/documents/{document_id}/status",
            headers=auth_headers(),
        )
        response.raise_for_status()
        last = response.json()["status"]
        if last in ("COMPLETED", "FAILED"):
            return last
        time.sleep(POLL_INTERVAL_SECONDS)
    return f"TIMEOUT (last seen {last})"


def verify_only():
    if not MANIFEST_PATH.exists():
        sys.exit(f"{MANIFEST_PATH} not found; nothing to verify. Run a full ingest first.")
    manifest = json.loads(MANIFEST_PATH.read_text())
    not_indexed = stamp_kb_statuses(manifest, wait=False)
    for name, entry in sorted(manifest.items()):
        print(f"  {entry['kb_status']:<12} {name}")
    print(f"\nRewrote {MANIFEST_PATH}")
    if not_indexed:
        sys.exit(f"{len(not_indexed)} document(s) not INDEXED: {', '.join(sorted(not_indexed))}")


def main():
    parser = argparse.ArgumentParser(description="Ingest the eval corpus via the running API.")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="re-check kb_status for documents already in the manifest; uploads nothing",
    )
    args = parser.parse_args()

    if args.verify_only:
        verify_only()
        return

    pdfs = sorted(CORPUS_DIR.glob("*.pdf"))
    if not pdfs:
        sys.exit("No PDFs in eval/corpus/. Run: uv run python -m eval.generate_corpus")

    # Every run uploads under fresh document ids, so ingesting on top of an existing
    # index leaves two copies of each file under different S3 URIs. Retrieval can then
    # return the old URI while the manifest holds the new one, scoring a correct
    # pipeline as a miss — with nothing failing loudly. Refuse instead.
    already_indexed = list_kb_documents()
    if already_indexed:
        print(f"The Knowledge Base already holds {len(already_indexed)} document(s):")
        for uri in list(already_indexed)[:5]:
            print(f"  {uri}")
        if len(already_indexed) > 5:
            print(f"  ... and {len(already_indexed) - 5} more")
        sys.exit(
            "\nIngesting now would index a second copy of every file and silently "
            "corrupt retrieval and citation scores.\nClear it first:\n"
            "  uv run python -m eval.reset_corpus --yes"
        )

    print(f"Uploading {len(pdfs)} document(s) to {API_BASE}\n")
    manifest = {}
    app_failures = []

    with httpx.Client(timeout=60.0) as client:
        for path in pdfs:
            print(f"{path.name} ... ", end="", flush=True)
            document_id = upload(client, path)
            status = wait_for_status(client, document_id)
            s3_key = f"uploads/{document_id}/{path.name}"
            verify_key_exists(s3_key)
            manifest[path.name] = {
                "document_id": document_id,
                "s3_uri": f"s3://{settings.s3_bucket_name}/{s3_key}",
                "status": status,
            }
            # Persisted per document: a failure on file 5 of 8 must not orphan the
            # four already-ingested documents with no record of where they went
            write_manifest(manifest)
            print(status)
            if status != "COMPLETED":
                app_failures.append(path.name)

    print("\nWaiting for the Knowledge Base to finish indexing...")
    not_indexed = stamp_kb_statuses(manifest, wait=True)
    for name, entry in sorted(manifest.items()):
        print(f"  {entry['kb_status']:<12} {name}")
    print(f"\nWrote {MANIFEST_PATH}")

    if app_failures or not_indexed:
        problems = sorted(set(app_failures) | set(not_indexed))
        sys.exit(
            f"\n{len(problems)} document(s) are not fully indexed: {', '.join(problems)}\n"
            "The harness will refuse to run until every expected document is INDEXED "
            "(or is invoked with --allow-unindexed)."
        )


if __name__ == "__main__":
    main()
