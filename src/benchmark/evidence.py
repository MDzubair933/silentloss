"""Closed local experiment records. These files observe, never recover, orders."""
import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_publication_evidence(plan_path, trace_path, closure_path):
    plan = json.loads(Path(plan_path).read_text())
    closure = json.loads(Path(closure_path).read_text())
    rows = [json.loads(line) for line in Path(trace_path).read_text().splitlines() if line.strip()]
    rows = [r for r in rows if r["run_id"] == plan["run_id"] and r["scenario_id"] == plan["scenario_id"]]
    expected = {s["index"] for s in plan["steps"]}
    if (len(rows) != len(expected) or {r["step_index"] for r in rows} != expected
            or closure.get("plan_sha256") != digest(plan)
            or closure.get("trace_sha256") != digest(sorted(rows, key=lambda r:r["step_index"]))):
        raise ValueError("Incomplete, duplicated, or altered experiment evidence")
    by_index = {r["step_index"]:r for r in rows}
    proofs = {}
    for step in plan["steps"]:
        r = by_index[step["index"]]
        if (r["order_id"] != step["order_id"] or r["request_body"] != step["payload"]
                or r["kind"] != step["kind"] or r["scheduled_fault"] != step["scheduled_fault"]):
            raise ValueError("Trace does not match planned request")
        e = r.get("fault_execution_evidence") or {}
        if (step["kind"] == "STANDARD" and step["scheduled_fault"] == "SQS_INJECTED_TIMEOUT"
                and r["status_code"] == 504 and r.get("db_outcome") == "CONFIRMED"
                and e.get("order_id") == step["order_id"]
                and e.get("correlation_id") == r["correlation_id"]
                and e.get("no_send_proven") is True):
            # Any other request for this order needs independent exclusion evidence;
            # do not claim U for replayed or deliberately duplicated orders.
            if sum(s["order_id"] == step["order_id"] for s in plan["steps"]) != 1:
                continue
            proofs[step["order_id"]] = dict(e, run_closed=True, evidence_reference=str(Path(trace_path).resolve()))
    return plan["run_id"], proofs
