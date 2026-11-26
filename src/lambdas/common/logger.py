import json
import logging
import os
import sys
import time


class StructuredLogger:
    """Structured JSON logger emitting correlation IDs without raw sensitive payloads."""

    def __init__(self, service_name: str = "silentloss"):
        self.service_name = service_name
        self.logger = logging.getLogger(service_name)
        self.logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.logger.addHandler(handler)
            self.logger.propagate = False

    def _log(self, level: str, message: str, correlation_id: str = None, **kwargs):
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "service": self.service_name,
            "level": level,
            "message": message,
            "correlation_id": correlation_id or "NONE",
            **kwargs,
        }
        line = json.dumps(payload, default=str)
        if level == "ERROR":
            self.logger.error(line)
        elif level == "WARN":
            self.logger.warning(line)
        elif level == "DEBUG":
            self.logger.debug(line)
        else:
            self.logger.info(line)

    def info(self, message: str, correlation_id: str = None, **kwargs):
        self._log("INFO", message, correlation_id, **kwargs)

    def warn(self, message: str, correlation_id: str = None, **kwargs):
        self._log("WARN", message, correlation_id, **kwargs)

    def error(self, message: str, correlation_id: str = None, **kwargs):
        self._log("ERROR", message, correlation_id, **kwargs)

    def debug(self, message: str, correlation_id: str = None, **kwargs):
        self._log("DEBUG", message, correlation_id, **kwargs)
