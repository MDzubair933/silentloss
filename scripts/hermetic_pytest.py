"""Run local regression tests with network and real AWS API calls prohibited."""
import os
import socket
import sys
import uuid
from pathlib import Path

os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
                  AWS_SESSION_TOKEN="testing", AWS_EC2_METADATA_DISABLED="true",
                  AWS_CONFIG_FILE="NUL", AWS_SHARED_CREDENTIALS_FILE="NUL",
                  PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1")
os.environ.pop("AWS_PROFILE", None)
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def blocked(*args, **kwargs):
    raise RuntimeError("Network/AWS access prohibited during local verification")

socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked
from botocore.client import BaseClient
BaseClient._make_api_call = blocked
import pytest
temp = Path(__file__).resolve().parents[1] / ".local-verification" / ("pytest-" + uuid.uuid4().hex)
temp.parent.mkdir(parents=True, exist_ok=True)
raise SystemExit(pytest.main(["-p", "no:cacheprovider", "--basetemp", str(temp), *sys.argv[1:]]))
