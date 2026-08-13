import os
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


# Shared state object passed between every node in the graph
class QueryState(TypedDict):
    original_query: str
    rewritten_query: str
    retrieved_chunks: list[dict[str, Any]]
    answer: str
    citations: list[str]
    cited_chunk_indices: list[int]
    token_usage: dict[str, int]


# Forced tool use gives schema-validated JSON — and, unlike free text, makes the
# model name the chunks it actually used, so citation accuracy is measurable
# independently of retrieval hit rate.
ANSWER_TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "record_answer",
                "description": "Record the answer and the excerpts it was drawn from.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "answer": {
                                "type": "string",
                                "description": "The answer, or a statement that the "
                                "excerpts do not contain it.",
                            },
                            "cited_chunks": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "1-based numbers of the excerpts the answer "
                                "relies on. Empty if the answer is not in the excerpts.",
                            },
                        },
                        "required": ["answer", "cited_chunks"],
                    }
                },
            }
        }
    ],
    "toolChoice": {"tool": {"name": "record_answer"}},
}


class MalformedModelResponse(ValueError):
    """The forced tool-use response was absent, truncated, or missing its answer.

    Subclasses ValueError so existing handlers keep working, but lets callers catch
    exactly this instead of every ValueError the pipeline might raise.
    """


def parse_answer_tool_use(response: dict) -> dict:
    for block in response["output"]["message"]["content"]:
        if "toolUse" in block:
            return block["toolUse"]["input"]
    raise MalformedModelResponse("Model response contained no toolUse block")


def accumulate_usage(state: QueryState, response: dict) -> dict[str, int]:
    usage = response.get("usage", {})
    running = state.get("token_usage") or {"input_tokens": 0, "output_tokens": 0}
    return {
        "input_tokens": running["input_tokens"] + usage.get("inputTokens", 0),
        "output_tokens": running["output_tokens"] + usage.get("outputTokens", 0),
    }


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
    return {
        **state,
        "rewritten_query": rewritten,
        "token_usage": accumulate_usage(state, response),
    }


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
        "If the answer is not present, say so. "
        "Record your answer with the record_answer tool, listing in cited_chunks the "
        "numbers of the excerpts the answer actually relies on."
        f"\n\nExcerpts:\n{context}"
        f"\n\nQuestion: {state['original_query']}"
    )

    response = client.converse(
        modelId=settings.bedrock_model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        toolConfig=ANSWER_TOOL_CONFIG,
    )

    result = parse_answer_tool_use(response)
    # Read defensively — a response cut off at the token limit arrives with a partial
    # input object — but do not return the partial as if it were a real answer.
    answer = str(result.get("answer") or "").strip()

    # An empty answer is indistinguishable downstream from a genuine abstention: the
    # API would return 200 with answer "" and no sources, and the eval would score it
    # as a clean cited-nothing pass on a negative case. That silently credits a
    # technical failure as pipeline quality, which is the exact thing the harness
    # exists to prevent. Fail loudly so it is counted as an error instead.
    if response.get("stopReason") == "max_tokens" or not answer:
        raise MalformedModelResponse(
            "Model response was truncated or carried no answer "
            f"(stopReason={response.get('stopReason')!r}, answer_length={len(answer)})"
        )

    chunks = state["retrieved_chunks"]

    # Drop indices the model invented; an out-of-range citation is a hallucination,
    # not a source, and must not count toward citation accuracy.
    # `type(i) is int` rather than isinstance: bool subclasses int, so a model
    # returning [true] would otherwise pass the range check as index 1.
    cited_indices = sorted(
        {
            i
            for i in result.get("cited_chunks") or []
            if type(i) is int and 1 <= i <= len(chunks)
        }
    )
    citations = []
    for i in cited_indices:
        source = chunks[i - 1].get("source", "")
        if source and source not in citations:
            citations.append(source)

    return {
        **state,
        "answer": answer,
        "citations": citations,
        "cited_chunk_indices": cited_indices,
        "token_usage": accumulate_usage(state, response),
    }


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
    try:
        result = query_graph.invoke({
            "original_query": question,
            "rewritten_query": "",
            "retrieved_chunks": [],
            "answer": "",
            "citations": [],
            "cited_chunk_indices": [],
            "token_usage": {"input_tokens": 0, "output_tokens": 0},
        })
    finally:
        # In a finally on purpose. A node raising is exactly when the trace is worth
        # having, and the failure path is the one that skips the flush: /query turns the
        # exception into a 502 and, on ECS, the task can be SIGTERM'd on scale-down
        # before the buffer drains on its own. Traces sent before the response returns
        # has to mean all responses, not just successful ones.
        get_client().flush()
    return result