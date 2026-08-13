"""Run the ground-truth dataset through the query pipeline and score it.

    uv run python -m eval.harness
    uv run python -m eval.harness --only nordwind-jan-total
    uv run python -m eval.harness --allow-missing-pricing
    uv run python -m eval.harness --allow-unindexed   # run despite unindexed documents

Every number written here comes from an actual pipeline run. Nothing is
estimated, defaulted, or carried over between runs.

Metrics, and why each one is here:

  retrieval hit rate   did the expected document appear in the retrieved chunks?
                       The upper bound on everything downstream.
  citation accuracy    did the model cite the expected document? Distinct from
                       retrieval only because generator() names the chunks it
                       used; before that change this metric was a restatement of
                       retrieval hit rate.
  answer match         do the expected substrings appear in the answer? A weak
                       proxy, deliberately: real answer quality is the job of the
                       faithfulness judge, which is not built yet.
  cited nothing        on questions the corpus cannot answer, did the pipeline
                       cite nothing? Note what this does NOT check: the answer
                       text. A model that invents a total while citing nothing
                       scores as a pass here. Detecting that needs the judge.
  latency              wall clock per query.
  llm token cost       the two Converse calls priced at the rates in
                       eval/pricing.json. Excludes the embedding charge for the
                       retriever's own query, which is billed separately and is
                       not reported in the retrieve response, so the figure is a
                       floor on true per-query cost, not the whole of it.
"""

import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path(__file__).parent
DATASET_PATH = EVAL_DIR / "dataset.json"
MANIFEST_PATH = EVAL_DIR / "corpus_manifest.json"
PRICING_PATH = EVAL_DIR / "pricing.json"
RESULTS_DIR = EVAL_DIR / "results"


class PricingUnavailable(RuntimeError):
    pass


def normalise(text: str) -> str:
    # Collapse whitespace and drop thousands separators so "1,240.00" and
    # "1240.00" compare equal. Documented in dataset.json.
    return re.sub(r"\s+", " ", str(text)).replace(",", "").strip().lower()


def contains_all(answer: str, expected: list[str]) -> bool:
    haystack = normalise(answer)
    return all(normalise(needle) in haystack for needle in expected)


def load_dataset() -> list[dict]:
    return json.loads(DATASET_PATH.read_text())["cases"]


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        sys.exit(
            f"{MANIFEST_PATH} not found. Ingest the corpus first:\n"
            "  uv run python -m eval.ingest_corpus"
        )
    return json.loads(MANIFEST_PATH.read_text())


def load_pricing(allow_missing: bool) -> dict | None:
    if not PRICING_PATH.exists():
        if allow_missing:
            return None
        raise PricingUnavailable(f"{PRICING_PATH} not found")

    pricing = json.loads(PRICING_PATH.read_text())
    rates = {key: pricing.get(key) for key in ("input_per_1m_usd", "output_per_1m_usd")}

    # type(...) rather than isinstance: bool subclasses int, and a quoted "3.00" would
    # otherwise crash cost computation mid-run — after Bedrock has been billed and
    # before any results are written
    mistyped = [
        key for key, value in rates.items()
        if value is not None and type(value) not in (int, float)
    ]
    if mistyped:
        raise PricingUnavailable(
            f"{PRICING_PATH}: not a number: "
            + ", ".join(f"{key}={pricing[key]!r}" for key in mistyped)
            + ".\nRates must be JSON numbers (e.g. 3.0), not strings. "
            "--allow-missing-pricing does not bypass this: a configured-but-wrong "
            "rate is a mistake, not an opt-out."
        )

    missing = [key for key, value in rates.items() if value is None]
    if missing:
        if allow_missing:
            return None
        raise PricingUnavailable(
            f"{PRICING_PATH} has no value for: {', '.join(missing)}.\n"
            "Fill in the Bedrock rates for your region, or re-run with "
            "--allow-missing-pricing to skip the cost metric.\n"
            "The harness will not substitute a guessed rate."
        )
    return pricing


def compute_cost(token_usage: dict, pricing: dict | None) -> float | None:
    if pricing is None:
        return None
    return round(
        token_usage.get("input_tokens", 0) / 1_000_000 * pricing["input_per_1m_usd"]
        + token_usage.get("output_tokens", 0) / 1_000_000 * pricing["output_per_1m_usd"],
        6,
    )


def score_case(case: dict, state: dict, expected_uri: str | None, error: str | None = None) -> dict:
    sources = [chunk.get("source", "") for chunk in state.get("retrieved_chunks", [])]
    citations = state.get("citations", [])
    answer = state.get("answer", "")

    result = {
        "id": case["id"],
        "question": case["question"],
        "answerable": case["answerable"],
        "expected_source": expected_uri,
        "answer": answer,
        "retrieved_sources": sources,
        "citations": citations,
        "cited_chunk_indices": state.get("cited_chunk_indices", []),
        "token_usage": state.get("token_usage", {}),
        "error": error,
    }

    if error is not None:
        # Nothing was measured. Scoring an error as a miss (or, for a negative case,
        # as a pass — the empty failure state has no citations) would conflate
        # pipeline quality with harness/API flakiness. Every metric is None, so the
        # case drops out of every rate; errored_cases and the non-zero exit keep the
        # run visibly unclean.
        result["retrieval_hit"] = None
        result["citation_hit"] = None
        result["answer_match"] = None
        result["cited_nothing"] = None
    elif case["answerable"]:
        result["retrieval_hit"] = expected_uri in sources
        result["citation_hit"] = expected_uri in citations
        result["answer_match"] = contains_all(answer, case["expected_answer_contains"])
        result["cited_nothing"] = None
    else:
        # Nothing was retrievable that should have been, so hit/match do not apply.
        # Named for exactly what it measures: the answer text is not inspected, so
        # a fabricated answer with no citations still passes. The judge will close that.
        #
        # Derived from cited_chunk_indices, not citations: generator() builds citations
        # only from cited chunks whose source URI is non-empty, and the retriever leaves
        # source "" when a result carries no s3Location. Keying off citations therefore
        # scored "cited a chunk with no URI" as a clean abstention — a false pass.
        result["retrieval_hit"] = None
        result["citation_hit"] = None
        result["answer_match"] = None
        result["cited_nothing"] = not result["cited_chunk_indices"]

    return result


def rate(values: list[bool | None]) -> float | None:
    scored = [v for v in values if v is not None]
    if not scored:
        return None
    return round(sum(scored) / len(scored), 4)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(round(fraction * (len(ordered) - 1))), len(ordered) - 1)
    return round(ordered[index], 3)


def aggregate(results: list[dict], pricing: dict | None) -> dict:
    # Errored cases are excluded from latency too: a fast failure drags p50 down and
    # a full boto3 retry chain drags p95 up, and neither is "wall clock per query"
    latencies = [r["latency_seconds"] for r in results if not r.get("error")]
    answerable = [r for r in results if r["answerable"]]
    negatives = [r for r in results if not r["answerable"]]
    costs = [
        r["llm_token_cost_usd"] for r in results if r["llm_token_cost_usd"] is not None
    ]

    return {
        "cases": len(results),
        "answerable_cases": len(answerable),
        "negative_cases": len(negatives),
        # Errored cases carry None for every metric, so they are excluded from the
        # rates above; this count is how the run stays visibly unclean
        "errored_cases": sum(1 for r in results if r.get("error")),
        "retrieval_hit_rate": rate([r["retrieval_hit"] for r in results]),
        "citation_accuracy": rate([r["citation_hit"] for r in results]),
        "answer_match_rate": rate([r["answer_match"] for r in results]),
        "cited_nothing_rate": rate([r["cited_nothing"] for r in results]),
        "latency_seconds": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "mean": round(statistics.fmean(latencies), 3) if latencies else 0.0,
        },
        "input_tokens": sum(r["token_usage"].get("input_tokens", 0) for r in results),
        "output_tokens": sum(r["token_usage"].get("output_tokens", 0) for r in results),
        "llm_token_cost_usd": round(sum(costs), 6) if costs else None,
        "cost_excludes": "retriever query embedding, billed separately and not "
        "reported by the retrieve API",
        "cost_unavailable_reason": None if pricing else "no rates in eval/pricing.json",
    }


def format_summary(summary: dict) -> str:
    def pct(value):
        return "n/a" if value is None else f"{value * 100:.1f}%"

    latency = summary["latency_seconds"]
    cost = summary["llm_token_cost_usd"]
    cost_text = f"${cost:.4f}" if cost is not None else f"n/a ({summary['cost_unavailable_reason']})"
    errored = summary["errored_cases"]
    return "\n".join(
        [
            "",
            f"  cases                {summary['cases']} "
            f"({summary['answerable_cases']} answerable, {summary['negative_cases']} negative)"
            + (f"  [{errored} ERRORED — excluded from every rate]" if errored else ""),
            f"  retrieval hit rate   {pct(summary['retrieval_hit_rate'])}",
            f"  citation accuracy    {pct(summary['citation_accuracy'])}",
            f"  answer match rate    {pct(summary['answer_match_rate'])}",
            f"  cited nothing        {pct(summary['cited_nothing_rate'])}  (negatives only)",
            f"  latency p50 / p95    {latency['p50']}s / {latency['p95']}s",
            f"  tokens in / out      {summary['input_tokens']:,} / {summary['output_tokens']:,}",
            f"  llm token cost       {cost_text}",
            f"                       excludes {summary['cost_excludes']}",
            "",
        ]
    )


def main():
    parser = argparse.ArgumentParser(description="Run the query pipeline eval.")
    parser.add_argument("--only", help="run a single case by id")
    parser.add_argument(
        "--allow-missing-pricing",
        action="store_true",
        help="run without the cost metric instead of failing when rates are unset",
    )
    parser.add_argument(
        "--allow-unindexed",
        action="store_true",
        help="run even if some expected documents are not INDEXED in the Knowledge "
        "Base; their cases will miss and are stamped expected_source_unindexed",
    )
    args = parser.parse_args()

    cases = load_dataset()
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
        if not cases:
            sys.exit(f"No case with id {args.only!r}")

    manifest = load_manifest()
    try:
        pricing = load_pricing(args.allow_missing_pricing)
    except PricingUnavailable as exc:
        sys.exit(f"\n{exc}\n")

    # Gate on the Knowledge Base's own status, not the app's: the app reports
    # COMPLETED the moment the ingest call is accepted, and indexing is asynchronous
    # — it can still fail afterwards. `status` alone let a corrupt corpus through.
    old_schema = [name for name, entry in manifest.items() if "kb_status" not in entry]
    if old_schema:
        sys.exit(
            "corpus_manifest.json predates kb_status tracking, so indexing cannot be "
            "verified from it.\nRefresh it first (list calls only, no Bedrock spend):\n"
            "  uv run python -m eval.ingest_corpus --verify-only"
        )
    unindexed = {name for name, entry in manifest.items() if entry["kb_status"] != "INDEXED"}
    if unindexed:
        listing = ", ".join(
            f"{name} ({manifest[name]['kb_status']})" for name in sorted(unindexed)
        )
        if not args.allow_unindexed:
            sys.exit(
                f"Not INDEXED in the Knowledge Base: {listing}\n"
                "Their cases cannot pass, so the run would not measure pipeline "
                "quality. Fix ingestion, or re-run with --allow-unindexed to measure "
                "anyway (affected cases are stamped expected_source_unindexed)."
            )
        print(f"WARNING: running with unindexed documents: {listing}\n")

    # Imported here, not at module scope: importing query_graph builds the LangGraph
    # and initialises Langfuse, which the unit tests must not trigger.
    from app.pipeline.query_graph import run_query_pipeline

    results = []
    print(f"Running {len(cases)} case(s)\n")
    for case in cases:
        expected_uri = None
        if case["expected_source_filename"]:
            entry = manifest.get(case["expected_source_filename"])
            if entry is None:
                sys.exit(
                    f"Case {case['id']!r} expects {case['expected_source_filename']!r}, "
                    "which is not in the manifest."
                )
            expected_uri = entry["s3_uri"]

        started = time.perf_counter()
        # A throttle or a malformed tool-use response on one case must not discard
        # every case already paid for. Record it as a failure and keep going.
        error = None
        try:
            state = run_query_pipeline(case["question"])
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            state = {
                "answer": "",
                "retrieved_chunks": [],
                "citations": [],
                "cited_chunk_indices": [],
                "token_usage": {"input_tokens": 0, "output_tokens": 0},
            }
        elapsed = time.perf_counter() - started

        result = score_case(case, state, expected_uri, error=error)
        result["latency_seconds"] = round(elapsed, 3)
        result["llm_token_cost_usd"] = compute_cost(result["token_usage"], pricing)
        result["expected_source_unindexed"] = case["expected_source_filename"] in unindexed
        results.append(result)

        if error:
            marks = "ERROR"
        elif case["answerable"]:
            marks = "".join(
                [
                    "R" if result["retrieval_hit"] else ".",
                    "C" if result["citation_hit"] else ".",
                    "A" if result["answer_match"] else ".",
                ]
            )
        else:
            marks = "nocite" if result["cited_nothing"] else "CITED"
        print(f"  [{marks:>7}] {case['id']}  ({result['latency_seconds']}s)")
        if error:
            print(f"            {error}")

    summary = aggregate(results, pricing)
    print(format_summary(summary))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"{stamp}.json"
    out_path.write_text(
        json.dumps(
            {
                "run_at": datetime.now(timezone.utc).isoformat(),
                "pricing": pricing,
                "summary": summary,
                "results": results,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Wrote {out_path}")
    print("Legend: R retrieval hit, C citation hit, A answer match\n")

    if summary["errored_cases"]:
        # Results are already on disk; exit non-zero so a CI gate does not read a
        # depressed score as a genuine quality regression
        sys.exit(
            f"{summary['errored_cases']} case(s) errored — the rates above are not a "
            "clean measurement. See the 'error' field in the results file."
        )


if __name__ == "__main__":
    main()
