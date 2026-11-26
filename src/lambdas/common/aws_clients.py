import boto3
from botocore.config import Config

# One transport attempt per operation. Application/SQS retries are explicit;
# worker retries belong to the event-source mapping. Never multiply retry layers.
DYNAMODB_CONFIG = Config(
    connect_timeout=0.2,
    read_timeout=0.4,
    retries={"total_max_attempts": 1, "mode": "standard"},
)

SQS_CONFIG = Config(
    connect_timeout=0.2,
    read_timeout=0.4,
    retries={"total_max_attempts": 1, "mode": "standard"},
)

# The worker also checks remaining time before each record.
WORKER_DYNAMODB_CONFIG = Config(
    connect_timeout=0.2,
    read_timeout=0.4,
    retries={"total_max_attempts": 1, "mode": "standard"},
)


def get_remaining_time_ms(context) -> int:
    """Return remaining Lambda invocation budget in ms, or a generous default in unit tests."""
    if context is not None and hasattr(context, "get_remaining_time_in_millis"):
        try:
            return int(context.get_remaining_time_in_millis())
        except Exception:
            pass
    return 10000


def create_dynamodb_client(worker: bool = False):
    return boto3.client("dynamodb", config=WORKER_DYNAMODB_CONFIG if worker else DYNAMODB_CONFIG)


def create_sqs_client():
    return boto3.client("sqs", config=SQS_CONFIG)
