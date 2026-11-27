"""Score S2/S3 reports on the S3-5 hard cases against gold and rubric fixed before the run.

The scorer is deterministic: it compares the report inputs with the gold facts and searches the
report text (PDF text, plus the S3 brief) for the warnings each case requires. It is a pre-score;
a person confirms the interpretation criterion.
"""

import json
import re
from pathlib import Path

CRITERIA = ("numbers", "no_unavailable_numbers", "links", "warnings", "interpretation")


def load_cases(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = {k: v for k, v in raw.items() if not k.startswith("_")}
    for name, case in cases.items():
        missing = {"keyword", "start", "end", "data_available", "rubric"} - set(case)
        if missing:
            raise ValueError(f"{name}: gold entry lacks {sorted(missing)}")
    return cases


def _matches(patterns, text):
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def score_report(case, inputs, checks, text):
    """Return {criterion: bool} plus reasons for one report.

    case: gold entry with rubric; inputs: report-inputs.json; checks: common checker result;
    text: all reader-facing text (PDF pages and, for S3, the brief).
    """
    facts, reasons = inputs.get("facts") or {}, []
    rubric = case["rubric"]
    result = dict.fromkeys(CRITERIA, True)

    if bool(inputs.get("data_available")) != bool(case["data_available"]):
        result["numbers"] = False
        reasons.append("data_available differs from gold")
    elif case["data_available"]:
        peak = facts.get("peak") or {}
        expected_peak = case.get("peak") or {}
        if facts.get("total") != case.get("total"):
            result["numbers"] = False
            reasons.append(f"total {facts.get('total')} != gold {case.get('total')}")
        if (peak.get("at"), peak.get("count")) != (expected_peak.get("at"), expected_peak.get("count")):
            result["numbers"] = False
            reasons.append("peak differs from gold")
    for issue in checks.get("issues", []):
        if issue.get("kind") == "figures":
            result["numbers"] = False
        elif issue.get("kind") == "links":
            result["links"] = False
        reasons.append(f"checker {issue.get('kind')}: {issue.get('message', '')}")

    if not case["data_available"] and re.search(r"\d[\d.,]*\s+judul", text, re.IGNORECASE):
        result["no_unavailable_numbers"] = False
        reasons.append("headline count shown for an unavailable period")

    for group in rubric.get("required_warnings", []):
        if not _matches(group["patterns"], text):
            result["warnings"] = False
            reasons.append(f"missing warning: {group['name']}")
    for rule in rubric.get("forbidden", []):
        if _matches(rule["patterns"], text) and not _matches(rule.get("unless", []), text):
            result["warnings"] = False
            reasons.append(f"forbidden without explanation: {rule['name']}")

    result["interpretation"] = None  # confirmed by a person
    return {"criteria": result, "score": sum(1 for v in result.values() if v), "reasons": reasons}
