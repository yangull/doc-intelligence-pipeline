"""Delete every uploaded document from S3 and DynamoDB.

DESTRUCTIVE. Wipes the whole `uploads/` prefix, every DOC# item in the table, and
*every* document in the Knowledge Base data source — the KB wipe is not scoped to
`uploads/`, so anything indexed from another prefix goes too.

It exists because the Knowledge Base accumulated repeat uploads of the same two
test files, which makes citation accuracy unmeasurable: the same document is
indexed under several S3 URIs, so there is no single correct citation to assert.

    uv run python -m eval.reset_corpus           # dry run, prints what it would delete
    uv run python -m eval.reset_corpus --yes     # actually delete

The dry run exits 1 on purpose, so that chaining it (`reset_corpus && ingest_corpus`)
cannot be mistaken for a completed wipe. Only a real `--yes` run exits 0.
"""

import argparse
import sys
import time

from app.core.aws_clients import get_bedrock_agent_client, get_dynamodb_resource, get_s3_client
from app.core.config import settings

UPLOADS_PREFIX = "uploads/"
# delete_knowledge_base_documents accepts at most 10 identifiers per call
KB_DELETE_BATCH = 10
KB_DRAIN_TIMEOUT_SECONDS = 300
KB_DRAIN_POLL_SECONDS = 5


def list_kb_documents():
    # {s3_uri: status} — statuses matter because ingestion and deletion are both
    # asynchronous, and callers need to distinguish INDEXED from in-flight/FAILED
    bedrock_agent = get_bedrock_agent_client()
    documents = {}
    kwargs = {
        "knowledgeBaseId": settings.bedrock_kb_id,
        "dataSourceId": settings.bedrock_kb_data_source_id,
    }
    while True:
        response = bedrock_agent.list_knowledge_base_documents(**kwargs)
        for detail in response.get("documentDetails", []):
            uri = detail.get("identifier", {}).get("s3", {}).get("uri")
            if uri:
                documents[uri] = detail.get("status", "UNKNOWN")
        token = response.get("nextToken")
        if not token:
            return documents
        kwargs["nextToken"] = token


def delete_kb_documents(uris):
    bedrock_agent = get_bedrock_agent_client()
    problems = []
    for start in range(0, len(uris), KB_DELETE_BATCH):
        batch = uris[start : start + KB_DELETE_BATCH]
        response = bedrock_agent.delete_knowledge_base_documents(
            knowledgeBaseId=settings.bedrock_kb_id,
            dataSourceId=settings.bedrock_kb_data_source_id,
            documentIdentifiers=[
                {"dataSourceType": "S3", "s3": {"uri": uri}} for uri in batch
            ],
        )
        # Per-document status: a document that refuses to delete would otherwise be
        # reported as a clean wipe and then collide with the next ingestion run
        for detail in response.get("documentDetails", []):
            if detail.get("status") not in ("DELETING", "DELETED", "NOT_FOUND"):
                problems.append(
                    f"{detail.get('identifier', {}).get('s3', {}).get('uri', '?')}: "
                    f"{detail.get('status')} {detail.get('statusReason', '')}".strip()
                )
        print(f"  deleted {start + len(batch)}/{len(uris)} KB documents")
    return problems


def wait_for_kb_empty():
    # Deletion is asynchronous: the API returns DELETING and the documents linger in
    # the listing for a while. Returning before the index has actually drained is how
    # a "successful" wipe still leaves duplicates for the next ingestion run.
    deadline = time.monotonic() + KB_DRAIN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        remaining = list_kb_documents()
        if not remaining:
            print("  Knowledge Base index drained")
            return []
        time.sleep(KB_DRAIN_POLL_SECONDS)
    return [f"{len(list_kb_documents())} document(s) still indexed after "
            f"{KB_DRAIN_TIMEOUT_SECONDS}s"]


def list_s3_objects():
    s3 = get_s3_client()
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=settings.s3_bucket_name, Prefix=UPLOADS_PREFIX):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def scan_document_items():
    table = get_dynamodb_resource().Table(settings.dynamodb_table_name)
    items = []
    kwargs = {"ProjectionExpression": "PK, SK"}
    while True:
        response = table.scan(**kwargs)
        items.extend(
            item for item in response.get("Items", []) if str(item["PK"]).startswith("DOC#")
        )
        if "LastEvaluatedKey" not in response:
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def delete_s3_objects(keys):
    s3 = get_s3_client()
    problems = []
    for start in range(0, len(keys), 1000):
        batch = keys[start : start + 1000]
        response = s3.delete_objects(
            Bucket=settings.s3_bucket_name,
            Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
        )
        # Quiet=True suppresses successes, not errors: delete_objects reports per-key
        # failures in the response body rather than raising
        problems.extend(
            f"{err.get('Key')}: {err.get('Code')} {err.get('Message', '')}".strip()
            for err in response.get("Errors", [])
        )
        print(f"  deleted {start + len(batch)}/{len(keys)} S3 objects")
    return problems


def delete_dynamodb_items(items):
    table = get_dynamodb_resource().Table(settings.dynamodb_table_name)
    with table.batch_writer() as batch:
        for item in items:
            batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
    print(f"  deleted {len(items)} DynamoDB items")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="perform the deletion")
    args = parser.parse_args()

    kb_uris = list(list_kb_documents())
    keys = list_s3_objects()
    items = scan_document_items()

    print(f"S3 bucket:      {settings.s3_bucket_name}")
    print(f"DynamoDB table: {settings.dynamodb_table_name}")
    print(f"Knowledge Base: {settings.bedrock_kb_id} / {settings.bedrock_kb_data_source_id}")
    print(f"Region:         {settings.aws_region}\n")
    print(f"{len(kb_uris)} document(s) indexed in the Knowledge Base")
    print(f"{len(keys)} object(s) under s3://{settings.s3_bucket_name}/{UPLOADS_PREFIX}")
    for key in keys[:10]:
        print(f"  {key}")
    if len(keys) > 10:
        print(f"  ... and {len(keys) - 10} more")
    print(f"{len(items)} DynamoDB item(s) with PK starting DOC#")

    if not kb_uris and not keys and not items:
        print("\nNothing to delete.")
        return

    if not args.yes:
        print("\nDry run. Re-run with --yes to delete all of the above.")
        sys.exit(1)

    # KB first: removing the vectors is the part that actually affects retrieval,
    # and it identifies documents by S3 URI, so do it while the objects still exist
    print("\nDeleting:")
    problems = []
    if kb_uris:
        problems += delete_kb_documents(kb_uris)
        problems += wait_for_kb_empty()
    if keys:
        problems += delete_s3_objects(keys)
    if items:
        delete_dynamodb_items(items)

    if problems:
        print(f"\n{len(problems)} deletion(s) did not succeed:")
        for problem in problems:
            print(f"  {problem}")
        sys.exit(
            "Re-run until this reports nothing. Ingesting on top of a partial wipe "
            "leaves duplicate documents in the Knowledge Base, which silently corrupts "
            "retrieval and citation scores."
        )
    print("\nDone.")


if __name__ == "__main__":
    main()
