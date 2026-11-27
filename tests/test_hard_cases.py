"""S3-5 hard-case gold and the deterministic pre-scorer."""
from pathlib import Path

import pytest

from slmcp.hard_cases import load_cases, score_report

GOLD = Path(__file__).parent.parent / "benchmark" / "s3-hard-cases.json"
PASS = {"status": "pass", "issues": []}


def inputs(case, **change):
    facts = {"total": case.get("total"), "peak": case.get("peak")}
    return {"data_available": case["data_available"], "facts": {**facts, **change}}


@pytest.fixture(scope="module")
def cases():
    return load_cases(GOLD)


def test_gold_has_six_cases_with_rubric(cases):
    assert set(cases) == {"partial-coverage", "name-collision", "multi-topic", "low-volume",
                          "high-volume-comparison", "conflicting-context"}
    assert cases["partial-coverage"]["data_available"] is False
    assert cases["name-collision"]["total"] == 356 and cases["conflicting-context"]["context"] is True


def test_name_collision_needs_the_tottenham_warning(cases):
    case = cases["name-collision"]
    bare = score_report(case, inputs(case), PASS, "Ada 356 judul tentang ott. Puncak 15 September.")
    note = "356 judul. Kata kunci pendek ini juga cocok dengan Tottenham."
    warned = score_report(case, inputs(case), PASS, note)
    assert bare["criteria"]["warnings"] is False and "Tottenham" in bare["reasons"][0]
    assert warned["criteria"]["warnings"] is True and warned["score"] == 4


def test_bare_huge_percentage_fails_unless_the_tiny_baseline_is_explained(cases):
    case = cases["conflicting-context"]
    note = "Naik 79.150% dibanding periode sebelumnya. Sumber bertentangan."
    bare = score_report(case, inputs(case), PASS, note)
    explained = score_report(case, inputs(case), PASS,
                             "Naik 79.150%, tetapi basis pembanding sangat kecil. Sumber bertentangan.")
    withheld = score_report(case, inputs(case), PASS, "Persentase tidak ditampilkan. Sumber bertentangan.")
    assert bare["criteria"]["warnings"] is False
    assert explained["criteria"]["warnings"] is True and withheld["criteria"]["warnings"] is True


def test_numbers_and_coverage(cases):
    case = cases["high-volume-comparison"]
    assert score_report(case, inputs(case), PASS, "")["criteria"]["numbers"] is True
    wrong = score_report(case, inputs(case, total=1071), PASS, "")
    assert wrong["criteria"]["numbers"] is False and "1071" in wrong["reasons"][0]
    missing = cases["partial-coverage"]
    unavailable = {"data_available": False, "facts": {}}
    shown = score_report(missing, unavailable, PASS, "Data di luar cakupan. 120 judul.")
    assert shown["criteria"]["no_unavailable_numbers"] is False
    clean = score_report(missing, unavailable, PASS, "Data belum tersedia untuk periode ini.")
    assert clean["criteria"]["no_unavailable_numbers"] is True and clean["criteria"]["warnings"] is True


def test_checker_findings_map_to_criteria(cases):
    case = cases["low-volume"]
    checks = {"status": "fail", "issues": [{"kind": "links", "message": "wrong target"},
                                           {"kind": "figures", "message": "total missing"}]}
    result = score_report(case, inputs(case), checks, "jumlah kecil")
    assert result["criteria"]["links"] is False and result["criteria"]["numbers"] is False
