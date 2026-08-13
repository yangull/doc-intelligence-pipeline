import json

import pytest

from eval import harness

EXPECTED_URI = "s3://bucket/uploads/abc/invoice-nordwind-2401.pdf"
OTHER_URI = "s3://bucket/uploads/def/invoice-nordwind-2403.pdf"


def answerable_case(**overrides):
    case = {
        "id": "nordwind-jan-total",
        "question": "What is the total due on Nordwind invoice NW-2401?",
        "answerable": True,
        "expected_source_filename": "invoice-nordwind-2401.pdf",
        "expected_answer_contains": ["1,240.00", "EUR"],
    }
    case.update(overrides)
    return case


def negative_case(**overrides):
    case = {
        "id": "negative-unknown-vendor",
        "question": "What is the total on the Siemens invoice?",
        "answerable": False,
        "expected_source_filename": None,
        "expected_answer_contains": [],
    }
    case.update(overrides)
    return case


def pipeline_state(answer="", sources=(), citations=(), indices=(), usage=None):
    return {
        "answer": answer,
        "retrieved_chunks": [{"text": "...", "score": 0.5, "source": s} for s in sources],
        "citations": list(citations),
        "cited_chunk_indices": list(indices),
        "token_usage": usage or {"input_tokens": 0, "output_tokens": 0},
    }


def test_normalise_strips_thousands_separators_and_case():
    assert harness.normalise(" 1,240.00  EUR ") == "1240.00 eur"


def test_contains_all_matches_across_separator_styles():
    assert harness.contains_all("The total is 1240.00 EUR.", ["1,240.00", "EUR"])
    assert harness.contains_all("The total is 1,240.00 EUR.", ["1240.00"])
    assert not harness.contains_all("The total is 890.00 EUR.", ["1,240.00"])


def test_score_case_records_a_full_hit():
    state = pipeline_state(
        answer="The total due is 1,240.00 EUR.",
        sources=[EXPECTED_URI, OTHER_URI],
        citations=[EXPECTED_URI],
        indices=[1],
    )
    result = harness.score_case(answerable_case(), state, EXPECTED_URI)
    assert (result["retrieval_hit"], result["citation_hit"], result["answer_match"]) == (
        True,
        True,
        True,
    )
    assert result["cited_nothing"] is None


def test_retrieved_but_uncited_separates_the_two_metrics():
    # The whole point of the forced-tool-use change: retrieval can succeed while
    # the model draws its answer from a different chunk
    state = pipeline_state(
        answer="The total due is 1,240.00 EUR.",
        sources=[EXPECTED_URI, OTHER_URI],
        citations=[OTHER_URI],
        indices=[2],
    )
    result = harness.score_case(answerable_case(), state, EXPECTED_URI)
    assert result["retrieval_hit"] is True
    assert result["citation_hit"] is False
    assert result["answer_match"] is True


def test_score_case_records_a_retrieval_miss():
    state = pipeline_state(answer="I could not find it.", sources=[OTHER_URI])
    result = harness.score_case(answerable_case(), state, EXPECTED_URI)
    assert result["retrieval_hit"] is False
    assert result["citation_hit"] is False
    assert result["answer_match"] is False


def test_negative_case_passes_when_nothing_is_cited():
    state = pipeline_state(answer="The excerpts do not mention Siemens.", sources=[OTHER_URI])
    result = harness.score_case(negative_case(), state, None)
    assert result["cited_nothing"] is True
    # Hit rates are undefined for a question with no correct source
    assert result["retrieval_hit"] is None
    assert result["citation_hit"] is None
    assert result["answer_match"] is None


def test_cited_nothing_does_not_inspect_the_answer_text():
    # Known limitation, pinned so it is not mistaken for abstention: a fabricated
    # answer with no citations still passes. Closing this needs the faithfulness
    # judge, which is why the metric is not called "abstention".
    state = pipeline_state(answer="The Siemens invoice totals 4,100.00 EUR.", sources=[OTHER_URI])
    result = harness.score_case(negative_case(), state, None)
    assert result["cited_nothing"] is True


def test_errored_case_scores_none_on_every_metric():
    # An error means nothing was measured; a miss would conflate pipeline quality
    # with API flakiness. None drops the case out of every rate.
    result = harness.score_case(
        answerable_case(), pipeline_state(), EXPECTED_URI, error="ThrottlingException: rate"
    )
    assert result["error"] == "ThrottlingException: rate"
    assert result["retrieval_hit"] is None
    assert result["citation_hit"] is None
    assert result["answer_match"] is None
    assert result["cited_nothing"] is None


def test_errored_negative_case_does_not_count_as_a_pass():
    # Regression: the substituted failure state has citations == [], and computing
    # `not citations` scored an errored negative as a clean cited_nothing pass —
    # three throttled negatives would have reported cited_nothing_rate 100%
    result = harness.score_case(
        negative_case(), pipeline_state(), None, error="ThrottlingException: rate"
    )
    assert result["cited_nothing"] is None


def test_negative_case_fails_when_the_model_cites_something():
    state = pipeline_state(
        answer="The Siemens invoice totals 890.00 EUR.",
        sources=[OTHER_URI],
        citations=[OTHER_URI],
        indices=[1],
    )
    result = harness.score_case(negative_case(), state, None)
    assert result["cited_nothing"] is False


def test_negative_case_fails_when_the_cited_chunk_has_no_source_uri():
    # Regression: generator() drops cited chunks whose source is "" when building
    # citations, and the retriever leaves source "" for a result with no s3Location.
    # Scoring `not citations` therefore passed a case where the model really did
    # cite something — a false pass, the worst direction for an eval to be wrong in.
    state = pipeline_state(
        answer="The Siemens invoice totals 890.00 EUR.",
        sources=[""],
        citations=[],
        indices=[1],
    )
    result = harness.score_case(negative_case(), state, None)
    assert result["cited_nothing"] is False


def test_rate_ignores_inapplicable_cases():
    assert harness.rate([True, False, None, True]) == pytest.approx(0.6667, abs=1e-4)
    assert harness.rate([None, None]) is None
    assert harness.rate([]) is None


def test_percentile_picks_order_statistics():
    values = [1.0, 2.0, 3.0, 4.0, 100.0]
    assert harness.percentile(values, 0.50) == 3.0
    assert harness.percentile(values, 0.95) == 100.0
    assert harness.percentile([], 0.5) == 0.0


def test_compute_cost_prices_both_directions():
    pricing = {"input_per_1m_usd": 3.0, "output_per_1m_usd": 15.0}
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000}
    assert harness.compute_cost(usage, pricing) == pytest.approx(4.5)


def test_compute_cost_is_none_without_rates():
    assert harness.compute_cost({"input_tokens": 10, "output_tokens": 5}, None) is None


def write_pricing(tmp_path, monkeypatch, payload):
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(harness, "PRICING_PATH", path)
    return path


def test_load_pricing_refuses_to_guess_missing_rates(tmp_path, monkeypatch):
    write_pricing(tmp_path, monkeypatch, {"input_per_1m_usd": None, "output_per_1m_usd": None})
    with pytest.raises(harness.PricingUnavailable, match="input_per_1m_usd"):
        harness.load_pricing(allow_missing=False)


def test_load_pricing_refuses_when_file_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "PRICING_PATH", tmp_path / "nope.json")
    with pytest.raises(harness.PricingUnavailable):
        harness.load_pricing(allow_missing=False)


def test_load_pricing_rejects_quoted_rates_even_with_opt_out(tmp_path, monkeypatch):
    # pricing.json is hand-edited next to string fields, so "3.00" is an easy typo;
    # it must fail before any Bedrock spend, and the missing-pricing opt-out must not
    # bypass it — a configured-but-wrong rate is a mistake, not an opt-out
    write_pricing(tmp_path, monkeypatch, {"input_per_1m_usd": "3.00", "output_per_1m_usd": 15.0})
    with pytest.raises(harness.PricingUnavailable, match="not a number"):
        harness.load_pricing(allow_missing=False)
    with pytest.raises(harness.PricingUnavailable, match="not a number"):
        harness.load_pricing(allow_missing=True)


def test_load_pricing_rejects_booleans(tmp_path, monkeypatch):
    # Same bool-subclasses-int trap as cited_chunks: true is not a rate
    write_pricing(tmp_path, monkeypatch, {"input_per_1m_usd": True, "output_per_1m_usd": 15.0})
    with pytest.raises(harness.PricingUnavailable, match="not a number"):
        harness.load_pricing(allow_missing=False)


def test_load_pricing_opt_out_returns_none(tmp_path, monkeypatch):
    write_pricing(tmp_path, monkeypatch, {"input_per_1m_usd": None, "output_per_1m_usd": None})
    assert harness.load_pricing(allow_missing=True) is None


def test_load_pricing_returns_configured_rates(tmp_path, monkeypatch):
    write_pricing(tmp_path, monkeypatch, {"input_per_1m_usd": 3.0, "output_per_1m_usd": 15.0})
    assert harness.load_pricing(allow_missing=False)["input_per_1m_usd"] == 3.0


def test_aggregate_reports_each_metric_over_its_own_denominator():
    results = [
        {
            "answerable": True,
            "retrieval_hit": True,
            "citation_hit": True,
            "answer_match": False,
            "cited_nothing": None,
            "latency_seconds": 2.0,
            "llm_token_cost_usd": 0.01,
            "token_usage": {"input_tokens": 100, "output_tokens": 20},
        },
        {
            "answerable": True,
            "retrieval_hit": True,
            "citation_hit": False,
            "answer_match": True,
            "cited_nothing": None,
            "latency_seconds": 4.0,
            "llm_token_cost_usd": 0.02,
            "token_usage": {"input_tokens": 200, "output_tokens": 30},
        },
        {
            "answerable": False,
            "retrieval_hit": None,
            "citation_hit": None,
            "answer_match": None,
            "cited_nothing": True,
            "latency_seconds": 3.0,
            "llm_token_cost_usd": 0.03,
            "token_usage": {"input_tokens": 50, "output_tokens": 10},
        },
    ]
    summary = harness.aggregate(results, {"input_per_1m_usd": 3.0, "output_per_1m_usd": 15.0})
    assert summary["cases"] == 3
    assert summary["answerable_cases"] == 2
    assert summary["negative_cases"] == 1
    assert summary["retrieval_hit_rate"] == 1.0
    assert summary["citation_accuracy"] == 0.5
    assert summary["answer_match_rate"] == 0.5
    assert summary["cited_nothing_rate"] == 1.0
    assert summary["input_tokens"] == 350
    assert summary["output_tokens"] == 60
    assert summary["llm_token_cost_usd"] == pytest.approx(0.06)
    assert summary["cost_unavailable_reason"] is None


def test_aggregate_counts_errored_cases_and_warns_in_the_summary():
    # Errored cases are excluded from every rate, so the count has to be visible or
    # a mostly-errored run would look like a clean (if small) measurement
    results = [
        {
            "answerable": True,
            "retrieval_hit": None,
            "citation_hit": None,
            "answer_match": None,
            "cited_nothing": None,
            "latency_seconds": 1.0,
            "llm_token_cost_usd": None,
            "token_usage": {"input_tokens": 0, "output_tokens": 0},
            "error": "ThrottlingException: rate exceeded",
        }
    ]
    summary = harness.aggregate(results, None)
    assert summary["errored_cases"] == 1
    # With nothing measured, the rates are None rather than 0% — a 0% would read as
    # a catastrophic quality regression instead of an inconclusive run
    assert summary["retrieval_hit_rate"] is None
    assert "ERRORED" in harness.format_summary(summary)


def test_aggregate_excludes_errored_cases_from_latency():
    # A fast failure would drag p50 down (and a retry chain would drag p95 up);
    # neither is "wall clock per query"
    measured = {
        "answerable": True,
        "retrieval_hit": True,
        "citation_hit": True,
        "answer_match": True,
        "cited_nothing": None,
        "latency_seconds": 5.0,
        "llm_token_cost_usd": None,
        "token_usage": {"input_tokens": 10, "output_tokens": 2},
        "error": None,
    }
    fast_failure = {
        "answerable": True,
        "retrieval_hit": None,
        "citation_hit": None,
        "answer_match": None,
        "cited_nothing": None,
        "latency_seconds": 0.2,
        "llm_token_cost_usd": None,
        "token_usage": {"input_tokens": 0, "output_tokens": 0},
        "error": "ValidationException: bad request",
    }
    summary = harness.aggregate([measured, fast_failure], None)
    assert summary["latency_seconds"]["p50"] == 5.0
    assert summary["latency_seconds"]["mean"] == 5.0


def test_aggregate_reports_zero_errors_on_a_clean_run():
    results = [
        {
            "answerable": True,
            "retrieval_hit": True,
            "citation_hit": True,
            "answer_match": True,
            "cited_nothing": None,
            "latency_seconds": 1.0,
            "llm_token_cost_usd": None,
            "token_usage": {"input_tokens": 10, "output_tokens": 2},
            "error": None,
        }
    ]
    summary = harness.aggregate(results, None)
    assert summary["errored_cases"] == 0
    assert "ERRORED" not in harness.format_summary(summary)


def test_aggregate_flags_why_cost_is_missing():
    results = [
        {
            "answerable": True,
            "retrieval_hit": True,
            "citation_hit": True,
            "answer_match": True,
            "cited_nothing": None,
            "latency_seconds": 1.0,
            "llm_token_cost_usd": None,
            "token_usage": {"input_tokens": 10, "output_tokens": 2},
        }
    ]
    summary = harness.aggregate(results, None)
    assert summary["llm_token_cost_usd"] is None
    assert summary["cost_unavailable_reason"] == "no rates in eval/pricing.json"
    assert "n/a" in harness.format_summary(summary)
