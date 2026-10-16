"""The template evaluator pass: real rendered lines in, per-language scores out (no judge calls here)."""

import importlib.util

import pytest

from earshot.eval import templates_eval as T

needs_live = pytest.mark.skipif(importlib.util.find_spec("earshot.live") is None, reason="needs the live app")


@needs_live
def test_every_language_gets_sentences_the_product_aired():
    rows, coverage = T.items([26])
    assert set(coverage) == {"en", "es", "ar", "hi"}
    for lang, c in coverage.items():
        assert c["aired"] > 40, lang
        assert c["aired"] + c["rendered"] + c["not_judged"] == 156
    ar = [r for r in rows if r["language"] == "ar"]
    assert any("؀" <= ch <= "ۿ" for r in ar for ch in r["text"])  # Arabic script, not English
    assert {r["how"] for r in rows} <= {"aired", "rendered"}


def test_summary_per_language_lowest_first():
    rows = [
        {"language": "hi", "template_id": "a.1", "mode": "casual", "text": "x", "how": "aired",
         "fluency": {"score": 2.0, "reason": "awkward"}, "coherence": {"score": 3.0}},
        {"language": "hi", "template_id": "b.1", "mode": "casual", "text": "y", "how": "aired",
         "fluency": {"score": 4.0, "reason": "fine"}, "coherence": {"error": "Timeout"}},
    ]  # fmt: skip
    s = T.summarize(rows)["hi"]
    assert s["fluency"] == {"judged": 2, "mean": 3.0, "min": 2.0, "below_pass": 1, "errors": 0}
    assert s["coherence"]["errors"] == 1
    assert s["lowest"][0]["template_id"] == "a.1"
