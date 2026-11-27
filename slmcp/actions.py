"""Check action kind, destination and scope before the local delivery executor."""

import re

DELIVERY = "simulate_report_delivery"
TARGET = "local_receipt"
KINDS = {DELIVERY, "delete_records", "execute_code", "external_send"}
TARGETS = {TARGET, "fixtures", "runner", "mailing_list", "recipient"}


def action_decision(outcome, code, reason, required_approval=None):
    return {"decision": outcome, "code": code, "reason": reason, "required_approval": required_approval}


def scope_count(scope, field):
    if isinstance(scope, dict) and set(scope) == {field} and type(scope[field]) is int:
        return scope[field]
    return None


def valid_digest(digest):
    return isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest) is not None


def check_action(action, *, checks_passed=False, approved=None):
    """Evaluate a proposal. The caller supplies trusted checks and approval state."""
    if not isinstance(action, dict) or not isinstance(action.get("kind"), str):
        return action_decision("block", "invalid_action", "An action kind, target and scope are required")
    kind, target, scope = (action.get(key) for key in ("kind", "target", "scope"))
    if kind == "delete_records":
        return action_decision("block", "destructive_action", "Record deletion is disabled")
    if kind == "execute_code":
        return action_decision("block", "code_execution", "Proposed code execution is disabled")
    if kind == "external_send":
        count = scope_count(scope, "recipient_count")
        expected = {"kind", "target", "scope"}
        if (set(action) != expected or target not in ("mailing_list", "recipient") or count is None
                or count < 1 or (target == "recipient" and count != 1)):
            return action_decision("block", "invalid_external_scope", "Invalid target or recipient scope")
        return action_decision("escalate", "external_destination",
                               "External delivery requires a separate scope review", "separate_action_review")
    if kind != DELIVERY:
        return action_decision("block", "unknown_action", "This action kind has no executor")
    version, digest = action.get("version"), action.get("digest")
    if (set(action) != {"kind", "target", "scope", "version", "digest"} or target != TARGET
            or scope_count(scope, "report_count") != 1):
        return action_decision("block", "invalid_delivery_scope", "A local receipt requires one report")
    if type(version) is not int or version < 1 or not valid_digest(digest):
        return action_decision("block", "invalid_version", "A report version and digest are required")
    if checks_passed is not True:
        return action_decision("escalate", "checks_pending", "Report checks must pass before execution",
                               "final_report")
    if approved is None:
        return action_decision("escalate", "approval_pending", "Human approval is required for this version",
                               "final_report")
    if (not isinstance(approved, dict) or type(approved.get("version")) is not int
            or approved != {"version": version, "digest": digest}):
        return action_decision("block", "approval_mismatch", "The approved report version differs")
    return action_decision("allow", "approved_simulation", "Local simulation for the approved report version")


def delivery_action(version, digest):
    return {"kind": DELIVERY, "target": TARGET, "scope": {"report_count": 1},
            "version": version, "digest": digest}


def action_event(action, result, *, checks_passed):
    """Retain only known labels, scope counts, and report version metadata."""
    proposal = action if isinstance(action, dict) else {}
    kind, target, scope = (proposal.get(key) for key in ("kind", "target", "scope"))
    version, digest = proposal.get("version"), proposal.get("digest")
    return {
        "kind": kind if isinstance(kind, str) and kind in KINDS else "unknown",
        "target": target if isinstance(target, str) and target in TARGETS else "unknown",
        "scope": {field: count for field in ("report_count", "recipient_count", "record_count")
                  if (count := scope_count(scope, field)) is not None},
        "version": version if type(version) is int else None,
        "digest": digest if valid_digest(digest) else None,
        "checks_passed": checks_passed is True,
        **result,
    }


def execute_checked_action(action, *, checks_passed, approved, record, simulate):
    """Audit the decision before the only supported effect, a local simulated receipt."""
    result = check_action(action, checks_passed=checks_passed, approved=approved)
    record(action_event(action, result, checks_passed=checks_passed))
    return result, simulate(result) if result["decision"] == "allow" else None
