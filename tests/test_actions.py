import json

import pytest
from test_final_review import bundle, event_kinds

from slmcp.actions import delivery_action, execute_checked_action
from slmcp.final_review import PENDING, FinalReviews, ReviewConflict

DIGEST = "a" * 64
PROPOSAL = delivery_action(1, DIGEST)
APPROVAL = {"version": 1, "digest": DIGEST}
MASS_DELETE = {"kind": "delete_records", "target": "fixtures", "scope": {"record_count": 50}}
CODE = {"kind": "execute_code", "target": "runner", "scope": {}}
BULK_SEND = {"kind": "external_send", "target": "mailing_list", "scope": {"recipient_count": 25}}


@pytest.mark.parametrize(("action", "checked", "approval", "expected", "code", "requirement"), [
    (PROPOSAL, True, APPROVAL, "allow", "approved_simulation", None),
    (MASS_DELETE, True, APPROVAL, "block", "destructive_action", None),
    (CODE, True, APPROVAL, "block", "code_execution", None),
    (BULK_SEND, True, APPROVAL, "escalate", "external_destination", "separate_action_review"),
    (PROPOSAL, False, APPROVAL, "escalate", "checks_pending", "final_report"),
    (PROPOSAL, True, None, "escalate", "approval_pending", "final_report"),
    (PROPOSAL, True, {**APPROVAL, "version": 2}, "block", "approval_mismatch", None),
    ({**PROPOSAL, "target": "private-label"}, True, APPROVAL, "block", "invalid_delivery_scope", None),
    ({**PROPOSAL, "scope": {"report_count": True}}, True, APPROVAL, "block", "invalid_delivery_scope", None),
    ({**BULK_SEND, "target": "recipient"}, True, APPROVAL, "block", "invalid_external_scope", None),
])
def test_action_gate_records_before_dispatch(action, checked, approval, expected, code, requirement):
    observed = []

    def record(event):
        observed.append(event)

    def simulate(result):
        assert observed and observed[0]["decision"] == result["decision"]
        observed.append("effect")
        return "receipt"

    result, receipt = execute_checked_action(action, checks_passed=checked, approved=approval,
                                             record=record, simulate=simulate)
    assert result["decision"] == expected
    assert result["required_approval"] == requirement
    assert observed[0]["code"] == code and observed[0]["reason"] == result["reason"]
    assert "private-label" not in json.dumps(observed)
    if expected == "allow":
        assert receipt == "receipt" and observed[1:] == ["effect"]
    else:
        assert receipt is None and observed[1:] == []


def test_unavailable_audit_stops_execution():
    def record(_):
        raise OSError("audit unavailable")

    def simulate(_):
        pytest.fail("The action executed before a decision was recorded")

    with pytest.raises(OSError, match="audit unavailable"):
        execute_checked_action(PROPOSAL, checks_passed=True, approved=APPROVAL,
                               record=record, simulate=simulate)


def test_final_review_audits_wrong_digest_before_recording_one_receipt(tmp_path):
    store = FinalReviews(tmp_path / "reviews.sqlite3")
    report = store.publish(bundle(tmp_path / "run"))
    case = report["review_id"]
    with pytest.raises(ReviewConflict, match="submitted digest"):
        store.decide(report["token"], "0" * 64, "approve", "")
    current = store.get(review_id=case)
    assert current["state"] == PENDING and current["receipt"] is None
    stopped, = store.action_trace(case)
    assert stopped["code"] == "approval_mismatch"
    assert stopped["scope"] == {"report_count": 1}
    assert event_kinds(store, case) == ["published", "action_checked"]

    approved = store.decide(report["token"], report["digest"], "approve", "")
    assert approved["receipt"]["mode"] == "simulation"
    assert [entry["decision"] for entry in store.action_trace(case)] == ["block", "allow"]
    assert event_kinds(store, case) == ["published", "action_checked", "action_checked", "approved",
                                        "simulated_delivery"]
