import pytest

from app.pipeline import query_graph
from app.pipeline.query_graph import MalformedModelResponse


def tool_use_response(answer, cited_chunks, input_tokens=100, output_tokens=20):
    return {
        "output": {
            "message": {
                "content": [
                    {
                        "toolUse": {
                            "name": "record_answer",
                            "input": {"answer": answer, "cited_chunks": cited_chunks},
                        }
                    }
                ]
            }
        },
        "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens},
    }


class FakeBedrock:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def make_state(**overrides):
    state = {
        "original_query": "What is the total?",
        "rewritten_query": "invoice total amount due",
        "retrieved_chunks": [
            {"text": "Total due 1,240.00 EUR", "score": 0.9, "source": "s3://b/uploads/1/a.pdf"},
            {"text": "Total due 890.00 EUR", "score": 0.7, "source": "s3://b/uploads/2/b.pdf"},
            {"text": "Receipt total 47.60", "score": 0.4, "source": "s3://b/uploads/3/c.pdf"},
        ],
        "answer": "",
        "citations": [],
        "cited_chunk_indices": [],
        "token_usage": {"input_tokens": 0, "output_tokens": 0},
    }
    state.update(overrides)
    return state


def run_generator(monkeypatch, response, state=None):
    fake = FakeBedrock(response)
    monkeypatch.setattr(query_graph, "get_bedrock_client", lambda: fake)
    return query_graph.generator(state or make_state()), fake


def test_generator_uses_forced_tool_choice(monkeypatch):
    _, fake = run_generator(monkeypatch, tool_use_response("1,240.00 EUR", [1]))
    tool_config = fake.calls[0]["toolConfig"]
    assert tool_config["toolChoice"] == {"tool": {"name": "record_answer"}}
    assert tool_config["tools"][0]["toolSpec"]["name"] == "record_answer"


def test_generator_cites_only_the_chunks_the_model_named(monkeypatch):
    result, _ = run_generator(monkeypatch, tool_use_response("1,240.00 EUR", [1]))
    assert result["answer"] == "1,240.00 EUR"
    assert result["cited_chunk_indices"] == [1]
    # The old behaviour cited all three retrieved sources
    assert result["citations"] == ["s3://b/uploads/1/a.pdf"]


def test_generator_drops_out_of_range_indices(monkeypatch):
    # Chunk 9 does not exist; citing it is a hallucination, not a source
    result, _ = run_generator(monkeypatch, tool_use_response("...", [2, 9, 0, -1]))
    assert result["cited_chunk_indices"] == [2]
    assert result["citations"] == ["s3://b/uploads/2/b.pdf"]


def test_generator_deduplicates_sources_from_the_same_document(monkeypatch):
    state = make_state(
        retrieved_chunks=[
            {"text": "part one", "score": 0.9, "source": "s3://b/uploads/1/a.pdf"},
            {"text": "part two", "score": 0.8, "source": "s3://b/uploads/1/a.pdf"},
        ]
    )
    result, _ = run_generator(monkeypatch, tool_use_response("...", [1, 2]), state)
    assert result["cited_chunk_indices"] == [1, 2]
    assert result["citations"] == ["s3://b/uploads/1/a.pdf"]


def test_generator_rejects_booleans_in_cited_chunks(monkeypatch):
    # bool subclasses int, so isinstance(True, int) is True and `1 <= True <= n`
    # passes: [true] would otherwise be accepted as a citation of chunk 1
    result, _ = run_generator(monkeypatch, tool_use_response("...", [True, False]))
    assert result["cited_chunk_indices"] == []
    assert result["citations"] == []


def test_generator_rejects_a_truncated_tool_response(monkeypatch):
    # A response cut off at max tokens arrives with a partial input object. Returning
    # it as answer "" with no citations would be indistinguishable from a genuine
    # abstention: the API 200s with an empty answer, and the eval scores a technical
    # failure as a clean cited-nothing pass. It must raise so it counts as an error.
    response = {
        "output": {"message": {"content": [{"toolUse": {"name": "record_answer", "input": {}}}]}},
        "usage": {"inputTokens": 10, "outputTokens": 5},
    }
    with pytest.raises(MalformedModelResponse, match="truncated or carried no answer"):
        run_generator(monkeypatch, response)


def test_generator_rejects_a_max_tokens_stop_reason(monkeypatch):
    # Even when the tool input looks complete, stopReason max_tokens means the model
    # was cut off and the answer cannot be trusted to be whole
    response = tool_use_response("The total is 1,240.00 EU", [1])
    response["stopReason"] = "max_tokens"
    with pytest.raises(MalformedModelResponse, match="truncated or carried no answer"):
        run_generator(monkeypatch, response)


def test_generator_rejects_a_blank_answer(monkeypatch):
    # Whitespace-only is empty for this purpose
    with pytest.raises(MalformedModelResponse, match="truncated or carried no answer"):
        run_generator(monkeypatch, tool_use_response("   ", [1]))


def test_generator_tolerates_null_cited_chunks(monkeypatch):
    result, _ = run_generator(monkeypatch, tool_use_response("...", None))
    assert result["cited_chunk_indices"] == []


def test_generator_supports_citing_nothing(monkeypatch):
    result, _ = run_generator(
        monkeypatch, tool_use_response("The excerpts do not contain that.", [])
    )
    assert result["citations"] == []
    assert result["cited_chunk_indices"] == []


def test_generator_raises_when_no_tool_use_block(monkeypatch):
    response = {
        "output": {"message": {"content": [{"text": "here is the answer"}]}},
        "usage": {"inputTokens": 10, "outputTokens": 5},
    }
    with pytest.raises(MalformedModelResponse, match="no toolUse block"):
        run_generator(monkeypatch, response)


def test_generator_accumulates_token_usage_onto_prior_nodes(monkeypatch):
    state = make_state(token_usage={"input_tokens": 300, "output_tokens": 40})
    result, _ = run_generator(
        monkeypatch, tool_use_response("...", [1], input_tokens=100, output_tokens=20), state
    )
    assert result["token_usage"] == {"input_tokens": 400, "output_tokens": 60}


def test_query_rewriter_records_token_usage(monkeypatch):
    response = {
        "output": {"message": {"content": [{"text": "  rewritten query  "}]}},
        "usage": {"inputTokens": 55, "outputTokens": 11},
    }
    monkeypatch.setattr(query_graph, "get_bedrock_client", lambda: FakeBedrock(response))
    result = query_graph.query_rewriter(make_state())
    assert result["rewritten_query"] == "rewritten query"
    assert result["token_usage"] == {"input_tokens": 55, "output_tokens": 11}


def test_accumulate_usage_tolerates_missing_usage_and_state():
    assert query_graph.accumulate_usage({}, {}) == {"input_tokens": 0, "output_tokens": 0}
