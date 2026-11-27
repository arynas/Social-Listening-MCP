"""Limit the public demo to a local receipt for an approved report."""

import re


def check_action(action, *, approved=None):
    """Check a proposal against the current human-approved version before simulated delivery."""
    if not isinstance(action, dict) or set(action) != {"kind", "target", "version", "digest"}:
        return {"decision": "block", "reason": "A report action requires a target and version"}
    if action["kind"] != "simulate_report_delivery" or action["target"] != "local_receipt":
        return {"decision": "block", "reason": "Only a simulated local report receipt is supported"}
    digest = action["digest"]
    if (type(action["version"]) is not int or action["version"] < 1 or not isinstance(digest, str)
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None):
        return {"decision": "block", "reason": "The action needs a version and a valid report digest"}
    if approved is None:
        return {"decision": "awaiting_approval", "reason": "Human approval is required for this version"}
    if approved != {"version": action["version"], "digest": digest}:
        return {"decision": "block", "reason": "The action does not match the approved report version"}
    return {"decision": "allow", "reason": "Local simulation for the approved report version"}
