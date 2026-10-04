"""JSON structured logging shared by the API and the exporter."""

import contextvars
import json
import logging
import sys
import time
import traceback

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

# Attributes every LogRecord carries; anything else passed via `extra=` is emitted as a field.
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "context": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        if record.exc_info and record.exc_info[1] is not None:
            entry["error"] = repr(record.exc_info[1])
            entry["stack"] = "".join(traceback.format_exception(*record.exc_info))
        for key, value in vars(record).items():
            if key not in _RESERVED and key not in entry:
                entry[key] = value
        return json.dumps(entry, default=str)


def configure_logging(level: str = "info") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Request logging is done by our middleware with request IDs and timings.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
