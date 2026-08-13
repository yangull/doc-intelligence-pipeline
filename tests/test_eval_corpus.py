"""Guard against drift between the corpus, the dataset, and the manifest.

The dataset asserts exact values (totals, invoice numbers, clause figures) that
live in the committed PDFs. If someone edits eval/generate_corpus.py and
regenerates without updating eval/dataset.json, the eval would quietly start
scoring against values no document contains. These tests fail instead.

No AWS calls: everything here reads local files.
"""

import json
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from eval import harness

CORPUS_DIR = Path(__file__).parent.parent / "eval" / "corpus"
DATASET_PATH = Path(__file__).parent.parent / "eval" / "dataset.json"

CASES = json.loads(DATASET_PATH.read_text())["cases"]
ANSWERABLE = [c for c in CASES if c["answerable"]]


def pdf_text(filename: str) -> str:
    reader = PdfReader(str(CORPUS_DIR / filename))
    return re.sub(r"\s+", " ", " ".join(page.extract_text() for page in reader.pages))


def test_every_referenced_document_exists():
    for case in ANSWERABLE:
        assert (CORPUS_DIR / case["expected_source_filename"]).exists(), case["id"]


def test_case_ids_are_unique():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))


def test_dataset_has_negative_cases():
    # Without them the eval cannot tell abstention from a lucky retrieval miss
    assert any(not c["answerable"] for c in CASES)


def test_negative_cases_declare_no_source_or_expectations():
    for case in (c for c in CASES if not c["answerable"]):
        assert case["expected_source_filename"] is None, case["id"]
        assert case["expected_answer_contains"] == [], case["id"]
        assert case["absent_terms"], f"{case['id']} needs absent_terms to be drift-guarded"


@pytest.mark.parametrize(
    "case", [c for c in CASES if not c["answerable"]], ids=lambda c: c["id"]
)
def test_negative_cases_are_still_unanswerable(case):
    # A negative case is only valid while the corpus really lacks the subject. If a
    # document is added that mentions it, abstention silently becomes the wrong
    # answer and the metric starts punishing a correct pipeline.
    #
    # Every remaining document has a text layer, so extracted text covers the whole
    # corpus. generate_corpus.py used to be folded into the haystack because the
    # scanned invoice's content existed only as string literals there; that document
    # is gone, so the extra false-alarm surface is no longer worth carrying.
    corpus = " ".join(pdf_text(p.name) for p in sorted(CORPUS_DIR.glob("*.pdf")))
    haystack = harness.normalise(corpus)
    for term in case["absent_terms"]:
        assert harness.normalise(term) not in haystack, (
            f"{case['id']}: {term!r} now appears in the corpus, "
            "so the question is no longer unanswerable"
        )


@pytest.mark.parametrize("case", ANSWERABLE, ids=lambda c: c["id"])
def test_expected_values_appear_in_the_source_document(case):
    haystack = harness.normalise(pdf_text(case["expected_source_filename"]))
    for needle in case["expected_answer_contains"]:
        assert harness.normalise(needle) in haystack, (
            f"{case['id']}: {needle!r} is not in {case['expected_source_filename']}"
        )


def test_corpus_contains_only_expected_files():
    on_disk = {p.name for p in CORPUS_DIR.glob("*")}
    assert all(name.endswith(".pdf") for name in on_disk), on_disk
    assert len(on_disk) == 6
