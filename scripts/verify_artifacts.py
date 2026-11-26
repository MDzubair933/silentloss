"""Check the clean SAM package, source correspondence and isolated imports locally."""
import json
from pathlib import Path
import subprocess
import sys
import argparse

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--build-dir", type=Path, default=root / ".aws-sam" / "final-build")
build = parser.parse_args().build_dir.resolve()
expected = {
    "requirements.txt",
    "common/__init__.py",
    "common/aws_clients.py",
    "common/logger.py",
    "common/models.py",
    "common/outbox.py",
    "common/validation.py",
    "handlers/__init__.py",
    "handlers/order_service.py",
    "handlers/outbox_relay.py",
    "handlers/outbox_sweeper.py",
    "handlers/fulfillment_worker.py",
}
results = []
work = root / ".local-verification" / "isolated-imports"
work.mkdir(parents=True, exist_ok=True)
for name, module in [
    ("OrderServiceFunction", "handlers.order_service"),
    ("OutboxRelayFunction", "handlers.outbox_relay"),
    ("OutboxRecoverySweeperFunction", "handlers.outbox_sweeper"),
    ("FulfillmentWorkerFunction", "handlers.fulfillment_worker"),
    ("ExperimentOrderServiceFunction", "handlers.order_service"),
]:
    artifact = build / name
    files = {p.relative_to(artifact).as_posix() for p in artifact.rglob("*") if p.is_file()}
    assert files == expected, (name, files)
    for rel in files:
        assert (artifact / rel).read_bytes() == (root / "src" / "lambdas" / rel).read_bytes(), rel
    code = """
import importlib,os,socket,sys
sys.dont_write_bytecode=True
def blocked(*a,**k): raise RuntimeError('Network/AWS prohibited')
socket.socket.connect=blocked;socket.socket.connect_ex=blocked
socket.create_connection=blocked;socket.getaddrinfo=blocked
from botocore.client import BaseClient
BaseClient._make_api_call=blocked
sys.path.insert(0,sys.argv[1])
handler=importlib.import_module(sys.argv[2])
assert callable(handler.handler)
assert not any(p.lower()==sys.argv[3].lower() for p in sys.path)
print('ISOLATED_IMPORT_OK')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(artifact), module, str(root)],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    results.append(
        {
            "function": name,
            "files": len(files),
            "source_matches": True,
            "isolated_import": result.stdout.strip(),
        }
    )
print(json.dumps(results, indent=2))
