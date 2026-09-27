"""Deterministic deduplication helpers for scheduler workflow failure Issues."""

import hashlib
import json
import re


def failed_step_names(steps):
    return sorted(
        name for name, data in (steps or {}).items()
        if isinstance(data, dict) and data.get("outcome") == "failure"
    )


def scheduler_failure_fingerprint(task, failed_steps, ict_date):
    payload = {"task": str(task), "failed_steps": sorted(set(failed_steps or [])), "ict_date": str(ict_date)}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def marker(fingerprint):
    return f"<!-- fp:{fingerprint} -->"


def latest_fingerprint(issue):
    comments = issue.get("comments") or []
    text = str(comments[-1].get("body", "")) if comments else str(issue.get("body", ""))
    match = re.search(r"<!-- fp:([0-9a-f]+) -->", text)
    return match.group(1) if match else None
