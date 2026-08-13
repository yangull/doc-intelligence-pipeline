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

# A raster scan on purpose, so pypdf finds nothing to check the dataset against. Its
# expected values are verified by reading the image, not by text extraction. The
# Knowledge Base reads it with a vision model at ingestion; this test file cannot.
NO_TEXT_LAYER = {"invoice-scanned-lowquality.pdf"}


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
    # The haystack includes eval/generate_corpus.py as well as the extracted PDF text:
    # the scanned invoice has no text layer, so its content exists only as string
    # literals in that source — without it, one document would be invisible to this
    # guard. If an absent term ever collides with unrelated code text, the test fails
    # safe (a false alarm to investigate, not a blind spot).
    corpus = " ".join(pdf_text(p.name) for p in sorted(CORPUS_DIR.glob("*.pdf")))
    generator_source = (CORPUS_DIR.parent / "generate_corpus.py").read_text()
    haystack = harness.normalise(corpus + " " + generator_source)
    for term in case["absent_terms"]:
        assert harness.normalise(term) not in haystack, (
            f"{case['id']}: {term!r} now appears in the corpus, "
            "so the question is no longer unanswerable"
        )


@pytest.mark.parametrize(
    "case", [c for c in ANSWERABLE if c["expected_source_filename"] not in NO_TEXT_LAYER],
    ids=lambda c: c["id"],
)
def test_expected_values_appear_in_the_source_document(case):
    haystack = harness.normalise(pdf_text(case["expected_source_filename"]))
    for needle in case["expected_answer_contains"]:
        assert harness.normalise(needle) in haystack, (
            f"{case['id']}: {needle!r} is not in {case['expected_source_filename']}"
        )


def test_scanned_document_really_has_no_text_layer():
    # If this starts failing, the hard case has silently become an easy one and the
    # parsing configuration is no longer being exercised by anything
    for filename in NO_TEXT_LAYER:
        assert pdf_text(filename).strip() == ""


def test_corpus_contains_only_expected_files():
    # Counted against the generator rather than a hardcoded number, which had to be
    # edited in lockstep whenever a builder was added or dropped. Each builder writes
    # exactly one PDF, so a leftover from a removed builder shows up as a mismatch —
    # and ingest_corpus.py globs *.pdf, so an orphan would be uploaded and evaluated.
    # (Name-level checking happens in generate_corpus.main(), which warns about any
    # file it did not write. Doing it here would mean writing files from a test.)
    from eval.generate_corpus import BUILDERS

    on_disk = {p.name for p in CORPUS_DIR.glob("*")}
    assert all(name.endswith(".pdf") for name in on_disk), on_disk
    assert len(on_disk) == len(BUILDERS), sorted(on_disk)
