import socket
import pytest
from botocore.client import BaseClient
from tests.stateful_double import StatefulAwsMock


@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    """Hermetic test environment fixture preventing live AWS calls or accidental network IO."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing-mock-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing-mock-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing-mock-session")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", "NUL")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "NUL")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("ORDERS_TABLE_NAME", "test-silentloss-orders")
    monkeypatch.setenv("IDEMPOTENCY_TABLE_NAME", "test-silentloss-idempotency")
    monkeypatch.setenv("OUTBOX_TABLE_NAME", "test-silentloss-outbox")
    monkeypatch.setenv("FULFILLMENT_TABLE_NAME", "test-silentloss-fulfillment")
    monkeypatch.setenv("REJECTION_AUDIT_TABLE_NAME", "test-silentloss-rejections")
    monkeypatch.setenv("FULFILLMENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123456789012/test-queue")
    monkeypatch.setenv("ENABLE_BENCHMARK_FAULT_ADAPTER", "false")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")


@pytest.fixture(autouse=True)
def block_outbound_sockets(monkeypatch):
    """Prevent accidental live network or AWS API socket connections in unit tests."""
    def guarded_create_connection(*args, **kwargs):
        raise RuntimeError("Network/AWS access blocked in hermetic unit tests")

    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_create_connection)
    monkeypatch.setattr(socket.socket, "connect", guarded_create_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_create_connection)
    monkeypatch.setattr(BaseClient, "_make_api_call", guarded_create_connection)


@pytest.fixture
def aws_double():
    """Fresh stateful thread-safe DynamoDB & SQS double per test."""
    return StatefulAwsMock()
