import json
import logging
import time
import boto3
from app.core.config import settings
from app.core.logging import setup_logging
from app.worker.extractor import process_document

logger = logging.getLogger(__name__)


def parse_sqs_message(message: dict) -> dict | None:
    """
    Extract document details from an SQS message.

    The message body contains an EventBridge event, which contains
    the S3 event details. We need to unwrap it to get the S3 key.

    The chain is:
    S3 → EventBridge → SQS → our worker
    So the message body is an EventBridge event wrapping an S3 event.
    """
    try:
        # SQS message body is a JSON string — parse it
        body = json.loads(message["Body"])

        # EventBridge puts S3 details in the "detail" field
        detail = body.get("detail", {})
        bucket = detail.get("bucket", {}).get("name")
        s3_key = detail.get("object", {}).get("key")

        if not bucket or not s3_key:
            logger.warning("Could not parse S3 details from message: %s", body)
            return None

        # Extract document_id and filename from the S3 key
        # Key format: "uploads/<document_id>/<filename>"
        parts = s3_key.split("/")
        if len(parts) < 3:
            logger.warning("Unexpected S3 key format: %s", s3_key)
            return None

        document_id = parts[1]
        filename = parts[2]

        return {
            "document_id": document_id,
            "s3_key": s3_key,
            "filename": filename,
        }

    except Exception as e:
        logger.warning("Error parsing SQS message: %s", e)
        return None


def run_worker():
    """
    Main worker loop — continuously polls SQS for new jobs.

    SQS uses "long polling" — instead of checking every second
    and getting empty responses, we ask SQS to wait up to 20 seconds
    for a message to arrive. This reduces costs and CPU usage.
    """
    sqs_client = boto3.client("sqs", region_name=settings.aws_region)

    logger.info("Worker started. Polling queue: %s", settings.sqs_queue_url)

    while True:
        try:
            # Ask SQS for up to 10 messages at once
            # WaitTimeSeconds=20 enables long polling
            response = sqs_client.receive_message(
                QueueUrl=settings.sqs_queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20,  # long polling
                MessageAttributeNames=["All"],
            )

            messages = response.get("Messages", [])

            if not messages:
                # No messages — loop back and poll again
                continue

            logger.info("Received %d message(s)", len(messages))

            for message in messages:
                receipt_handle = message["ReceiptHandle"]

                # Parse the message to get document details
                doc_details = parse_sqs_message(message)

                if doc_details and doc_details["filename"].endswith(".pdf"):
                    outcome = process_document(
                        document_id=doc_details["document_id"],
                        s3_key=doc_details["s3_key"],
                        filename=doc_details["filename"],
                    )

                    if outcome == "retry":
                        # Don't delete — SQS redelivers up to 3 times, then DLQ
                        logger.warning("Transient failure — message will be retried")
                    else:
                        # "completed" or "failed" (permanent) — either way,
                        # retrying won't change the result, so remove the message
                        sqs_client.delete_message(
                            QueueUrl=settings.sqs_queue_url,
                            ReceiptHandle=receipt_handle,
                        )
                else:
                    # Bad message format or non-PDF file — delete it so it doesn't block the queue
                    sqs_client.delete_message(
                        QueueUrl=settings.sqs_queue_url,
                        ReceiptHandle=receipt_handle,
                    )

        except KeyboardInterrupt:
            logger.info("Worker stopped by user")
            break
        except Exception as e:
            logger.error("Worker error: %s", e)
            # Wait before retrying to avoid hammering AWS on errors
            time.sleep(5)


if __name__ == "__main__":
    setup_logging()
    run_worker()
