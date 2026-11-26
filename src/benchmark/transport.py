import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


SENSITIVE_HEADER_KEYS = {"authorization", "x-amz-security-token", "x-amz-date", "x-api-key"}


def sanitize_headers_for_logging(headers: Dict[str, str]) -> Dict[str, str]:
    """Strip all authentication headers before persisting request telemetry."""
    return {
        k: v for k, v in (headers or {}).items()
        if k.lower() not in SENSITIVE_HEADER_KEYS
    }


class SigV4HttpTransport:
    """
    Real authenticated HTTP transport using AWS IAM SigV4 request signing.
    Signs requests in-memory via boto3 credentials without exposing secrets in logs.
    """

    def __init__(
        self,
        api_endpoint: str,
        region: str = "us-east-1",
        profile_name: Optional[str] = None,
        timeout_seconds: float = 8.0,
    ):
        self.api_endpoint = api_endpoint.rstrip("/")
        self.region = region
        self.profile_name = profile_name
        self.timeout_seconds = timeout_seconds
        self._session = None
        self._lock = threading.Lock()

    def _get_credentials(self):
        with self._lock:
            if self._session is None:
                self._session = boto3.Session(
                    profile_name=self.profile_name,
                    region_name=self.region,
                )
            creds = self._session.get_credentials()
            if creds is None:
                raise RuntimeError("No AWS credentials available for SigV4 HTTP transport")
            return creds.get_frozen_credentials()

    def request(
        self,
        method: str,
        path: str,
        headers: Dict[str, str],
        body_str: Optional[str] = None,
    ) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        """Execute a signed HTTP request and return (status_code, parsed_json, response_metadata)."""
        url = f"{self.api_endpoint}{path}"
        creds = self._get_credentials()

        aws_req = AWSRequest(
            method=method.upper(),
            url=url,
            data=body_str.encode("utf-8") if body_str is not None else None,
            headers=dict(headers),
        )
        SigV4Auth(creds, "execute-api", self.region).add_auth(aws_req)

        prepared = aws_req.prepare()
        req = urllib.request.Request(
            url=prepared.url,
            data=prepared.body,
            headers=dict(prepared.headers),
            method=prepared.method,
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw_resp = resp.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw_resp) if raw_resp.strip() else {}
                meta = {
                    "aws_apigw_request_id": resp.headers.get("Apigw-Requestid") or resp.headers.get("x-amzn-RequestId"),
                }
                return resp.status, parsed, meta
        except urllib.error.HTTPError as http_err:
            raw_resp = http_err.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw_resp) if raw_resp.strip() else {}
            except json.JSONDecodeError:
                parsed = {"raw_error": raw_resp[:200]}
            meta = {
                "aws_apigw_request_id": http_err.headers.get("Apigw-Requestid") if http_err.headers else None,
            }
            return http_err.code, parsed, meta
        except (urllib.error.URLError, TimeoutError, OSError) as url_err:
            # Timeout or network connectivity failure -> ambiguous client outcome
            return 0, {"error": "ClientTransportTimeoutOrNetworkError", "reason": type(url_err).__name__}, {}


class LocalHandlerTransport:
    """In-memory transport invoking local Lambda handler directly for hermetic verification."""

    def __init__(self, handler_invoker: Callable, controller_arn: Optional[str] = None):
        self.handler_invoker = handler_invoker
        self.controller_arn = controller_arn

    def request(
        self,
        method: str,
        path: str,
        headers: Dict[str, str],
        body_str: Optional[str] = None,
    ) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        mock_event = {
            "requestContext": {"http": {"method": method.upper()}},
            "httpMethod": method.upper(),
            "rawPath": path,
            "headers": dict(headers),
            "body": body_str,
        }
        if self.controller_arn:
            mock_event["requestContext"]["authorizer"] = {"iam": {"userArn": self.controller_arn}}
        res = self.handler_invoker(mock_event, None)
        status_code = int(res.get("statusCode", 500))
        raw_resp = res.get("body", "{}")
        parsed = json.loads(raw_resp) if isinstance(raw_resp, str) and raw_resp.strip() else {}
        return status_code, parsed, {"execution_mode": "local_in_memory_handler"}


class SqsDuplicateControl:
    """Explicitly republishes the SAME domain event; not a claim of natural SQS redelivery."""

    def __init__(self, queue_url, client):
        self.queue_url = queue_url
        self.client = client

    def __call__(self, event):
        return self.client.send_message(QueueUrl=self.queue_url, MessageBody=json.dumps(event))
