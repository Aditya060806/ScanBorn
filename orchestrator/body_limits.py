"""Refuse an upload body larger than its route accepts before it is spooled to disk.

Starlette parses a multipart body completely (spooling files to a temporary directory) before
the route runs, so a size check inside the route bounds memory but not disk. This middleware
refuses a declared Content-Length above the limit at once and counts the bytes of a body sent
without one, stopping it at the limit. Both answer 413 with code `too_large`.
"""
import json
import re

from capture.service.imports import MAX_IMPORT_BYTES
from capture.service.validation import MAX_CHUNK_BYTES

OVERHEAD = 64 * 1024  # multipart boundaries, part headers and query-free form fields
LIMITS = (
    (re.compile(r"^/capture$"), MAX_CHUNK_BYTES + OVERHEAD),
    (re.compile(r"^/capture(/[^/]+)?/import$"), MAX_IMPORT_BYTES + OVERHEAD),
)


class BodyTooLarge(Exception):
    pass


def limit_for(method: str, path: str) -> int | None:
    if method != "POST":
        return None
    return next((limit for pattern, limit in LIMITS if pattern.match(path)), None)


async def _refuse(send, limit: int) -> None:
    body = json.dumps({"detail": {
        "code": "too_large",
        "error": f"This upload is larger than the {limit} bytes this route accepts."}}).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        limit = limit_for(scope.get("method", ""), scope.get("path", "")) \
            if scope["type"] == "http" else None
        if limit is None:
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _refuse(send, limit)
            return

        state = {"received": 0, "exceeded": False, "started": False}

        async def counted_receive():
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > limit:
                    state["exceeded"] = True
                    raise BodyTooLarge()
            return message

        async def guarded_send(message):
            # Once the body ran over, whatever the route answers (FastAPI turns a failed form
            # parse into a 400) is replaced by the 413 that says why.
            if state["exceeded"]:
                return
            state["started"] = True
            await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except BodyTooLarge:
            pass
        if state["exceeded"] and not state["started"]:
            await _refuse(send, limit)
