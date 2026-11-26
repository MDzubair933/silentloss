from unittest.mock import MagicMock
from src.reconciliation.aws_acquisition import AwsEvidenceAcquirer
from src.reconciliation.collector import EvidenceReconciliationCollector
from src.reconciliation.models import OrderClassification, SourceCompleteness


def test_reconciliation_confirmed_silent_loss_vs_indeterminate():
    committed = {
        "ord_1": {"status": "FULFILLED", "dispatch_status": "CONFIRMED"},
        "ord_2": {"status": "COMMITTED", "dispatch_status": "REJECTED"},  # Stored dispatch metadata alone is inconclusive
        "ord_3": {"status": "COMMITTED", "dispatch_status": "UNKNOWN"},   # Ambiguous timeout -> I (NOT U!)
    }
    fulfillments = [
        {"order_id": "ord_1", "fulfillment_id": "FULFILLMENT#PRIMARY", "status": "COMPLETED"}
    ]

    collector = EvidenceReconciliationCollector()
    summary = collector.reconcile(committed, fulfillments)

    assert summary.total_committed == 3
    assert summary.total_fulfilled == 1
    assert summary.confirmed_silent_loss == 0
    assert summary.indeterminate_orders == 2
    assert summary.classified_orders["ord_2"] == OrderClassification.INDETERMINATE.value
    assert summary.classified_orders["ord_3"] == OrderClassification.INDETERMINATE.value
    assert summary.invariant_holds is True
    assert summary.clean_success_verified is False


def test_reconciliation_incomplete_downstream_defaults_to_indeterminate():
    committed = {
        "ord_1": {"status": "FULFILLED"},
        "ord_2": {"status": "COMMITTED", "dispatch_status": "REJECTED"},
    }
    fulfillments = [{"order_id": "ord_1", "fulfillment_id": "FULFILLMENT#PRIMARY", "status": "COMPLETED"}]
    sc = SourceCompleteness(fulfillment_table_complete=False)

    collector = EvidenceReconciliationCollector()
    summary = collector.reconcile(committed, fulfillments, source_completeness=sc)

    assert summary.confirmed_silent_loss == 0
    assert summary.indeterminate_orders == 1
    assert summary.clean_success_verified is False  # Zero U does NOT pass when I > 0!
    assert summary.source_completeness_percent < 100.0


def test_reconciliation_state_inconsistencies_detected():
    committed = {
        "ord_inconsistent_1": {"status": "FULFILLED"},  # Says FULFILLED, missing FulfillmentTable record!
        "ord_inconsistent_2": {"status": "FAILED"},     # Says FAILED, yet has COMPLETED fulfillment!
    }
    fulfillments = [
        {"order_id": "ord_inconsistent_2", "fulfillment_id": "FULFILLMENT#PRIMARY", "status": "COMPLETED"}
    ]
    collector = EvidenceReconciliationCollector()
    summary = collector.reconcile(committed, fulfillments, source_completeness=SourceCompleteness(fulfillment_table_complete=True))

    assert summary.indeterminate_orders == 2
    assert len(summary.state_inconsistencies) == 2
    assert summary.invariant_holds is True


def test_reconciliation_deduplicates_scans_and_ignores_rejected_attempts():
    committed = {"ord_1": {"status": "FULFILLED"}}
    fulfillments = [
        # Same fulfillment_id scanned twice -> deduplicated to 1!
        {"order_id": "ord_1", "fulfillment_id": "FULFILLMENT#PRIMARY", "status": "COMPLETED"},
        {"order_id": "ord_1", "fulfillment_id": "FULFILLMENT#PRIMARY", "status": "COMPLETED"},
        # Rejected attempt -> NEVER counted as duplicate completed effect!
        {"order_id": "ord_1", "fulfillment_id": "FULFILLMENT#RETRY_REJ", "status": "REJECTED"},
    ]
    collector = EvidenceReconciliationCollector()
    summary = collector.reconcile(committed, fulfillments)
    assert summary.total_fulfilled == 1
    assert summary.duplicate_completed_effects == 0

    # Two distinct COMPLETED fulfillment IDs for the same order -> 1 duplicate completed effect!
    fulfillments.append({"order_id": "ord_1", "fulfillment_id": "FULFILLMENT#SECOND", "status": "COMPLETED"})
    summary2 = collector.reconcile(committed, fulfillments)
    assert summary2.duplicate_completed_effects == 1


def test_reconciliation_pending_past_deadline_tracked():
    committed = {"ord_p": {"status": "COMMITTED"}}
    pending = {"ord_p": {"recoverable_path_verified": True, "age_seconds": 180, "evidence_reference":"test-durable-path"}}

    collector = EvidenceReconciliationCollector(observation_deadline_seconds=120)
    summary = collector.reconcile(committed, [], pending_records=pending)
    assert summary.total_pending == 1
    assert summary.pending_past_deadline == 1
    assert summary.clean_success_verified is False


def test_aws_acquirer_paginates_and_never_consumes_sqs(aws_double):
    # Seed 5 orders (page_size in StatefulAwsMock is 2 -> exercises 3 pages!)
    for i in range(5):
        oid = f"ord_run123__{i}"
        aws_double.put_item(
            "orders",
            {
                "order_id": {"S": oid},
                "status": {"S": "COMMITTED" if i == 4 else "FULFILLED"},
                "dispatch_status": {"S": "REJECTED" if i == 4 else "CONFIRMED"},
                "created_at": {"S": "2026-11-26T12:00:00Z"},
            },
        )
        if i < 4:
            aws_double.put_item(
                "fulfillments",
                {
                    "order_id": {"S": oid},
                    "fulfillment_id": {"S": "FULFILLMENT#PRIMARY"},
                    "status": {"S": "COMPLETED"},
                },
            )

    mock_session = MagicMock()
    mock_session.client.side_effect = lambda svc, **kwargs: aws_double

    acquirer = AwsEvidenceAcquirer(
        orders_table="orders",
        fulfillment_table="fulfillments",
        rejection_table="rejections",
        queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/q",
        dlq_url="https://sqs.us-east-1.amazonaws.com/123456789012/dlq",
        order_log_group="/aws/lambda/order",
        worker_log_group="/aws/lambda/worker",
        session=mock_session,
    )

    ev = acquirer.acquire_run_evidence(run_id="run123")
    assert aws_double.sqs_receive_called is False
    assert len(ev["committed_orders"]) == 5
    assert len(ev["fulfillment_records"]) == 4

    summary = EvidenceReconciliationCollector().reconcile(
        committed_orders=ev["committed_orders"],
        fulfillment_records=ev["fulfillment_records"],
        rejection_records=ev["rejection_order_ids"],
        publication_outcomes=ev["publication_outcomes"],
        source_completeness=ev["source_completeness"],
    )
    assert summary.total_committed == 5
    assert summary.total_fulfilled == 4
    assert summary.confirmed_silent_loss == 0
    assert summary.invariant_holds is True


def test_aws_acquirer_remote_timeouts_and_client_cache():
    mock_session = MagicMock()
    mock_client = MagicMock()
    mock_session.client.return_value = mock_client

    acquirer = AwsEvidenceAcquirer(
        "o", "f", "r", "q", "dlq", "", "", session=mock_session
    )
    assert acquirer.connect_timeout == 5.0
    assert acquirer.read_timeout == 10.0
    assert acquirer.max_attempts == 2

    c1 = acquirer._client("dynamodb")
    c2 = acquirer._client("dynamodb")
    assert c1 is c2
    assert mock_session.client.call_count == 1
    cfg = mock_session.client.call_args.kwargs["config"]
    assert cfg.connect_timeout == 5.0
    assert cfg.read_timeout == 10.0
    assert cfg.retries["total_max_attempts"] == 2
