import os
import logging
from typing import Any
from typing_extensions import TypedDict

# Must set credentials BEFORE importing langfuse — it initializes on import
from app.core.config import settings
os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_base_url)

from langgraph.graph import StateGraph, END
from langfuse import observe, get_client  # v3 API — langfuse_context no longer exists
from app.core.aws_clients import get_bedrock_client, get_bedrock_agent_runtime_client

logger = logging.getLogger(__name__)


# Shared state object passed between every node in the graph
class QueryState(TypedDict):
    original_query: str
    rewritten_query: str
    retrieved_chunks: list[dict[str, Any]]
    answer: str
    citations: list[str]


@observe()  # nested span under the root trace
def query_rewriter(state: QueryState) -> QueryState:
    client = get_bedrock_client()

    prompt = (
        "Rewrite the following question to improve retrieval from business documents "
        "(invoices, contracts, reports). Return ONLY the rewritten question."
        f"\n\nQuestion: {state['original_query']}"
    )

    response = client.converse(
        modelId=settings.bedrock_model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
    )
    rewritten = response["output"]["message"]["content"][0]["text"].strip()

    # {**state, ...} copies all existing state fields and overrides rewritten_query
    return {**state, "rewritten_query": rewritten}


@observe()  # nested span under the root trace
def retriever(state: QueryState) -> QueryState:
    client = get_bedrock_agent_runtime_client()

    response = client.retrieve(
        knowledgeBaseId=settings.bedrock_kb_id,
        retrievalQuery={"text": state["rewritten_query"]},
        retrievalConfiguration={
            "vectorSearchConfiguration": {"numberOfResults": 5}
        },
    )

    # Extract text, relevance score, and S3 source URI from each result
    chunks = [
        {
            "text": r["content"]["text"],
            "score": r.get("score", 0),
            "source": r.get("location", {}).get("s3Location", {}).get("uri", ""),
        }
        for r in response.get("retrievalResults", [])
    ]

    return {**state, "retrieved_chunks": chunks}


@observe()  # nested span under the root trace
def generator(state: QueryState) -> QueryState:
    client = get_bedrock_client()

    # Format retrieved chunks into a numbered context block for the prompt
    context = "\n\n".join(
        f"[Chunk {i + 1}]\n{c['text']}"
        for i, c in enumerate(state["retrieved_chunks"])
    )

    prompt = (
        "Answer the question using only the document excerpts below. "
        "If the answer is not present, say so."
        f"\n\nExcerpts:\n{context}"
        f"\n\nQuestion: {state['original_query']}"
    )

    response = client.converse(
        modelId=settings.bedrock_model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
    )
    answer = response["output"]["message"]["content"][0]["text"].strip()
    citations = [c["source"] for c in state["retrieved_chunks"] if c["source"]]

    return {**state, "answer": answer, "citations": citations}


def build_query_graph():
    graph = StateGraph(QueryState)

    # Register nodes
    graph.add_node("query_rewriter", query_rewriter)
    graph.add_node("retriever", retriever)
    graph.add_node("generator", generator)

    # Wire nodes in sequence
    graph.set_entry_point("query_rewriter")
    graph.add_edge("query_rewriter", "retriever")
    graph.add_edge("retriever", "generator")
    graph.add_edge("generator", END)

    return graph.compile()


query_graph = build_query_graph()


@observe(name="query_pipeline")  # root trace — nodes above become nested spans
def run_query_pipeline(question: str) -> QueryState:
    result = query_graph.invoke({
        "original_query": question,
        "rewritten_query": "",
        "retrieved_chunks": [],
        "answer": "",
        "citations": [],
    })
    # Force flush so traces are sent before the HTTP response returns
    get_client().flush()
    return result