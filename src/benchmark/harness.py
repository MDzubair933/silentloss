import concurrent.futures
import json
import os
import random
import time
import uuid
import re
from src.benchmark.evidence import digest, load_publication_evidence
from typing import Any, Callable, Dict, List, Optional

from src.benchmark.scenarios import BenchmarkScenario, STANDARD_SCENARIOS, validate_concurrency
from src.benchmark.transport import (
    LocalHandlerTransport,
    SigV4HttpTransport,
    sanitize_headers_for_logging,
)


class BenchmarkHarness:
    """
    Deterministic, interruption-safe benchmark harness for SilentLoss.
    Supports:
      - Real IAM SigV4 HTTP transport (live deployed API)
      - Local handler transport (hermetic testing)
      - Exact fault schedule selection via seeded RNG
      - Positive, negative, and duplicate replay controls
      - Incremental JSONL persistence (sanitized, zero credentials stored)
    """

    def __init__(
        self,
        api_endpoint: Optional[str] = None,
        handler_invoker: Optional[Callable] = None,
        region: str = "us-east-1",
        profile_name: Optional[str] = None,
        seed: int = 20261126,
        run_id: Optional[str] = None,
        controller_arn: Optional[str] = None,
        duplicate_sender=None,
        observer=None,
        convergence_seconds: float = 60,
    ):
        self.api_endpoint = api_endpoint
        self.handler_invoker = handler_invoker
        self.region = region
        self.profile_name = profile_name
        self.seed = seed
        self.run_id = run_id or uuid.uuid4().hex[:16]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", self.run_id):
            raise ValueError("run_id must contain 1-24 alphanumeric, underscore or hyphen characters")
        self.duplicate_sender = duplicate_sender
        self.observer = observer
        if not 0 < convergence_seconds <= 180:
            raise ValueError("Invalid convergence window")
        self.convergence_seconds = convergence_seconds

        if handler_invoker is not None:
            self.transport = LocalHandlerTransport(handler_invoker, controller_arn)
            self.transport_mode = "TRANSPORT_LOCAL_HANDLER"
        elif api_endpoint:
            self.transport = SigV4HttpTransport(
                api_endpoint=api_endpoint,
                region=region,
                profile_name=profile_name,
            )
            self.transport_mode = "TRANSPORT_LIVE_SIGV4"
        else:
            self.transport = None
            self.transport_mode = "DRY_RUN_ONLY"

    def build_deterministic_plan(self, scenario: BenchmarkScenario) -> List[Dict[str, Any]]:
        """
        Build a reproducible, seeded execution plan covering:
          - Standard orders
          - Exact percentage-matched fault schedule
          - Negative control malformed orders
          - Idempotent replay / duplicate deliveries
        """
        rng = random.Random(f"{self.seed}:{scenario.scenario_id}")
        items_plan: List[Dict[str, Any]] = []

        # Exact fault quota (fixes modulo bug when order_count != 100)
        fault_count = round((scenario.order_count * scenario.fault_rate_percent) / 100.0)
        fault_indices = set(rng.sample(range(scenario.order_count), min(fault_count, scenario.order_count)))

        for i in range(scenario.order_count):
            order_id = f"ord_{self.run_id}__{scenario.scenario_id}_{i:04d}"
            sku = "SKU_POISON_TEST" if i < scenario.poison_pill_count else f"SKU-{(i % 5) + 1:03d}"
            unit_price = "25.00"
            qty = 2
            total_amount = "50.00"

            payload = {
                "order_id": order_id,
                "customer_id": f"cust_{self.run_id}_{i:04d}",
                "items": [{"sku": sku, "quantity": qty, "unit_price": unit_price}],
                "total_amount": total_amount,
                "currency": "USD",
            }
            items_plan.append(
                {
                    "index": i,
                    "kind": "STANDARD",
                    "order_id": order_id,
                    "idempotency_key": order_id,
                    "payload": payload,
                    "scheduled_fault": scenario.fault_type if i in fault_indices else None,
                }
            )

        # Negative controls (invalid schemas expected to fail with HTTP 400)
        for j in range(scenario.invalid_negative_control_count):
            neg_id = f"ord_{self.run_id}__{scenario.scenario_id}_neg_{j:03d}"
            items_plan.append(
                {
                    "index": scenario.order_count + j,
                    "kind": "NEGATIVE_CONTROL",
                    "order_id": neg_id,
                    "idempotency_key": neg_id,
                    "payload": {
                        "order_id": neg_id,
                        "customer_id": "cust_neg",
                        "items": [{"sku": "SKU-001", "quantity": 1, "unit_price": "10.00"}],
                        "total_amount": "999.00",  # Intentional mismatch
                        "currency": "USD",
                    },
                    "scheduled_fault": None,
                }
            )

        # Duplicate / replay controls reusing existing order IDs and identical payloads
        for k in range(min(scenario.duplicate_replay_count, len(items_plan))):
            base = items_plan[k]
            if base["kind"] == "STANDARD":
                items_plan.append(
                    {
                        "index": len(items_plan),
                        "kind": "REPLAY_CONTROL",
                        "order_id": base["order_id"],
                        "idempotency_key": base["idempotency_key"],
                        "payload": dict(base["payload"]),
                        "scheduled_fault": None,
                    }
                )

        for k in range(scenario.duplicate_event_count):
            base = items_plan[k]
            items_plan.append(dict(base, index=len(items_plan), kind="DUPLICATE_EVENT", source_index=k, scheduled_fault=None))
        return items_plan

    def _dispatch_step(self, step, scenario, pacing_sec=0, source_result=None, deadline=None):
        correlation_id = f"corr_{self.run_id}_{scenario.scenario_id}_{step['index']:04d}"
        headers = {"Content-Type": "application/json", "x-correlation-id": correlation_id,
                   "Idempotency-Key": step["idempotency_key"]}
        if step["scheduled_fault"]:
            headers["x-benchmark-fault"] = step["scheduled_fault"]
        start = time.monotonic()
        attempts = []
        message_body = None
        try:
            if deadline is not None and start >= deadline:
                raise TimeoutError("Client campaign deadline reached")
            if step["kind"] == "DUPLICATE_EVENT":
                if self.duplicate_sender is None:
                    raise ValueError("Actual duplicate-event sender required")
                message_body = (source_result or {}).get("response_body", {}).get("published_event")
                if not message_body:
                    raise ValueError("Original published event unavailable")
                meta = self.duplicate_sender(message_body)
                code, body = 202, {"duplicate_event_sent": True, "sqs_message_id":meta.get("MessageId")}
            else:
                if self.transport is None:
                    raise ValueError("Transport required")
                limit = 2 if scenario.client_retry_on_ambiguous else 1
                for number in range(limit):
                    code, body, meta = self.transport.request("POST", "/experiments/orders", headers, json.dumps(step["payload"]))
                    attempts.append({"attempt":number+1,"status_code":code,"response_body":body,"metadata":meta})
                    if code != 0 or (deadline is not None and time.monotonic()+8 >= deadline):
                        break
        except Exception as exc:
            code,body,meta = 0,{"error":type(exc).__name__},{}
        e = body.get("fault_execution_evidence") or {}
        confirmed = (bool(step["scheduled_fault"]) and e.get("fault_adapter_executed") is True
                     and e.get("fault_type") == step["scheduled_fault"] and e.get("order_id") == step["order_id"])
        return {"run_id":self.run_id,"scenario_id":scenario.scenario_id,"step_index":step["index"],
                "kind":step["kind"],"order_id":step["order_id"],"correlation_id":correlation_id,
                "transport_mode":self.transport_mode,"status_code":code,"duration_ms":round((time.monotonic()-start)*1000,2),
                "client_acknowledged_commit":code in (200,201),"db_outcome":body.get("db_outcome"),
                "queue_outcome":body.get("queue_outcome"),"is_idempotent_replay":body.get("is_idempotent_replay",False),
                "scheduled_fault":step["scheduled_fault"],"fault_confirmed_executed":confirmed,
                "fault_execution_evidence":e or None,"error_code":body.get("error"),
                "aws_request_id":meta.get("aws_apigw_request_id"),"sanitized_headers":sanitize_headers_for_logging(headers),
                "request_body":step["payload"],"response_body":body,"attempts":attempts,"message_body":message_body,
                "timestamp":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}

    def run_scenario(
        self,
        scenario_id: str,
        max_concurrency: Optional[int] = None,
        pacing_sec: float = 0.0,
        incremental_jsonl_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        scenario = STANDARD_SCENARIOS.get(scenario_id)
        if not scenario:
            raise ValueError(f"Unknown scenario: {scenario_id}")

        concurrency = validate_concurrency(scenario.concurrency if max_concurrency is None else max_concurrency)
        if pacing_sec < 0 or pacing_sec > 10:
            raise ValueError("Invalid pacing interval")
        if scenario.duplicate_event_count and self.duplicate_sender is None:
            raise ValueError("B-04 requires an actual duplicate-event sender")
        deadline = time.monotonic() + 180
        plan = self.build_deterministic_plan(scenario)

        # Execute initial standard + negative controls concurrently, then replays cleanly
        primary_steps = [s for s in plan if s["kind"] not in ("REPLAY_CONTROL", "DUPLICATE_EVENT")]
        replay_steps = [s for s in plan if s["kind"] in ("REPLAY_CONTROL", "DUPLICATE_EVENT")]

        manifest = {"run_id":self.run_id,"seed":self.seed,"scenario_id":scenario_id,"steps":plan,"concurrency":concurrency,"pacing_sec":pacing_sec}
        if incremental_jsonl_path:
            os.makedirs(os.path.dirname(os.path.abspath(incremental_jsonl_path)), exist_ok=True)
            with open(incremental_jsonl_path+"."+scenario_id+".plan.json","x",encoding="utf-8") as f:
                json.dump(manifest,f,sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
        results: List[Dict[str, Any]] = []
        jsonl_file = None
        if incremental_jsonl_path:
            os.makedirs(os.path.dirname(os.path.abspath(incremental_jsonl_path)), exist_ok=True)
            jsonl_file = open(incremental_jsonl_path, "a", encoding="utf-8")

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = []
                for step in primary_steps:
                    if pacing_sec:
                        time.sleep(min(pacing_sec, max(0, deadline-time.monotonic())))
                    futures.append(executor.submit(self._dispatch_step, step, scenario, 0, None, deadline))
                for fut in concurrent.futures.as_completed(futures):
                    rec = fut.result()
                    results.append(rec)
                    if jsonl_file:
                        jsonl_file.write(json.dumps(rec) + "\n")
                        jsonl_file.flush()
                        os.fsync(jsonl_file.fileno())

            # Execute replay controls sequentially after primary commits settle
            for step in replay_steps:
                source = next((r for r in results if r["step_index"] == step.get("source_index")),None)
                rec = self._dispatch_step(step, scenario, 0, source, deadline)
                results.append(rec)
                if jsonl_file:
                    jsonl_file.write(json.dumps(rec) + "\n")
                    jsonl_file.flush()
                    os.fsync(jsonl_file.fileno())
        finally:
            if jsonl_file:
                jsonl_file.close()

        results.sort(key=lambda r: r["step_index"])

        if incremental_jsonl_path:
            with open(incremental_jsonl_path+"."+scenario_id+".closed.json","x",encoding="utf-8") as f:
                json.dump({"plan_sha256":digest(manifest),"trace_sha256":digest(results)},f)
                f.flush()
                os.fsync(f.fileno())
        observation = self.observer(self.run_id, self.convergence_seconds) if self.observer else None
        report = {
            "observation": observation,
            "convergence_observed": bool(observation and observation.get("convergence_observed")),
            "benchmark_verified": False,  # Request completion alone never certifies business outcomes.
            "duplicate_events_sent":sum(r["kind"] == "DUPLICATE_EVENT" and r["status_code"] == 202 for r in results),
            "run_id": self.run_id,
            "seed": self.seed,
            "scenario_id": scenario.scenario_id,
            "scenario_name": scenario.name,
            "transport_mode": self.transport_mode,
            "concurrency_used": concurrency,
            "total_requests": len(results),
            "standard_requests": sum(1 for r in results if r["kind"] == "STANDARD"),
            "negative_control_rejections": sum(1 for r in results if r["kind"] == "NEGATIVE_CONTROL" and r["status_code"] == 400),
            "replay_control_hits": sum(1 for r in results if r["kind"] == "REPLAY_CONTROL" and r["status_code"] == 200),
            "scheduled_faults": sum(1 for r in results if r["scheduled_fault"] is not None),
            "confirmed_executed_faults": sum(1 for r in results if r["fault_confirmed_executed"]),
            "throttled_429_count": sum(1 for r in results if r["status_code"] == 429),
            "execution_log": results,
        }
        if observation and incremental_jsonl_path:
            from src.reconciliation.collector import EvidenceReconciliationCollector
            _, proofs = load_publication_evidence(incremental_jsonl_path+"."+scenario_id+".plan.json",
                                                  incremental_jsonl_path,
                                                  incremental_jsonl_path+"."+scenario_id+".closed.json")
            expected = {s["order_id"] for s in plan if s["kind"] == "STANDARD"}
            orders = {oid:data for oid,data in observation["committed_orders"].items() if oid in expected}
            effects = [e for e in observation["fulfillment_records"] if e["order_id"] in expected]
            pending = {oid:p for oid,p in (observation.get("pending_records") or {}).items() if oid in expected}
            outbox_map = {oid:ob for oid,ob in (observation.get("outbox_records") or {}).items() if oid in expected}

            async_fault_evidence = {}
            confirmed_fault_total = 0
            for r in results:
                if r["fault_confirmed_executed"]:
                    confirmed_fault_total += 1
                    continue
                if r["kind"] == "STANDARD" and r["scheduled_fault"]:
                    ob = outbox_map.get(r["order_id"]) or {}
                    ev = ob.get("fault_execution_evidence") or {}
                    expected_blocks = 2 if r["scheduled_fault"] == "SQS_INJECTED_TIMEOUT" else 1
                    if (
                        ev.get("fault_adapter_executed") is True
                        and ev.get("fault_type") == r["scheduled_fault"]
                        and ev.get("order_id") == r["order_id"]
                        and ev.get("correlation_id") == r["correlation_id"]
                        and ev.get("blocked_attempts") == expected_blocks
                    ):
                        confirmed_fault_total += 1
                        async_fault_evidence[r["order_id"]] = ev
            if outbox_map:
                report["confirmed_executed_faults"] = confirmed_fault_total
                report["async_fault_evidence"] = async_fault_evidence

            fault_rec_times = [
                float(ob["recovery_time_sec"])
                for oid, ob in outbox_map.items()
                if oid in async_fault_evidence and ob.get("dispatch_state") == "CONFIRMED" and ob.get("recovery_time_sec") is not None
            ]
            max_rec_sec = round(max(fault_rec_times), 3) if fault_rec_times else 0.0

            sc = observation["source_completeness"]
            sc.publication_logs_complete = True  # Complete closed controller trace, not a CloudWatch claim.
            summary = EvidenceReconciliationCollector().reconcile(
                orders,
                effects,
                pending_records=pending,
                rejection_records=set(observation["rejection_order_ids"]) & expected,
                publication_evidence=proofs,
                source_completeness=sc,
                duplicate_delivery_attempts=report["duplicate_events_sent"],
                recovery_time_sec=max_rec_sec,
            )
            report["reconciliation"] = summary.to_dict()
            report["expected_commits_observed"] = set(orders) == expected
            controls_ok = (report["negative_control_rejections"] == scenario.invalid_negative_control_count
                           and report["replay_control_hits"] == scenario.duplicate_replay_count
                           and report["duplicate_events_sent"] == scenario.duplicate_event_count
                           and report["scheduled_faults"] == report["confirmed_executed_faults"])
            report["benchmark_verified"] = summary.clean_success_verified and set(orders) == expected and controls_ok
        return report
