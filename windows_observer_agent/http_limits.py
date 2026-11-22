"""HTTPX2 response hooks and bounded byte streams, before SDK decoding."""
import re

from httpx2 import AsyncByteStream

from .resource_limits import ResourceLimitError, check_json_shape

_NEWLINE = re.compile(rb'\r\n|\r|\n')
_UTF8_BOM = b'\xef\xbb\xbf'
_JSON_START = re.compile(rb'(?:\xef\xbb\xbf)?[ \t\r\n]*[{\["]')


class HttpResponseBudget:
    """One budget per MCP session (including reconnects) or model request."""

    def __init__(self, body_bytes, *, sse_bytes=None, total_bytes=None,
                 sse_events=None, requests=None):
        self.body_bytes = body_bytes
        self.sse_bytes = sse_bytes
        self.total_bytes = total_bytes
        self.sse_events = sse_events
        self.requests = requests
        self.received = self.events = self.sent = 0
        self.error = None

    def check(self):
        if self.error is not None:
            raise ResourceLimitError(self.error)

    def stop(self, code):
        if self.error is None:
            self.error = code
        self.check()

    async def guard_request(self, request):
        self.check()
        self.sent += 1
        if self.requests is not None and self.sent > self.requests:
            self.stop('http_request_count_limit')

    async def guard_response(self, response):
        self.check()
        encoding = response.headers.get('Content-Encoding', '').strip().casefold()
        if encoding not in ('', 'identity'):
            self.stop('http_encoding')
        media_type = response.headers.get('Content-Type', '').partition(';')[0].strip().lower()
        sse = self.sse_bytes is not None and media_type == 'text/event-stream'
        length = response.headers.get('Content-Length', '').strip().lstrip('0')
        if not sse and length.isascii() and length.isdecimal():
            # Compare digits without converting a hostile, very large integer.
            maximum = str(self.body_bytes)
            if (len(length), length) > (len(maximum), maximum):
                self.stop('http_body_limit')

        body = _BoundedBody(response.stream, self, sse, media_type)
        if response.is_stream_consumed:
            # Some in-memory transports supply preconstructed responses.
            list(body.push(response.content))
            list(body.finish())
        else:
            # HTTPX2 invokes this hook before send()/stream() reads the body.
            response.stream = body

    def account(self, size):
        self.check()
        self.received += size
        if self.total_bytes is not None and self.received > self.total_bytes:
            self.stop('http_session_limit')

    def event(self):
        self.events += 1
        if self.sse_events is not None and self.events > self.sse_events:
            self.stop('sse_event_count_limit')

    def shape(self, content):
        try:
            check_json_shape(content)
        except ResourceLimitError as exc:
            self.stop(exc.code)


class _BoundedBody(AsyncByteStream):
    def __init__(self, stream, budget, sse, media_type):
        self.stream = stream
        self.budget = budget
        self.frame = _SSEFrames(budget) if sse else None
        self.media_type = media_type
        self.body = bytearray()

    def push(self, chunk):
        self.budget.account(len(chunk))
        if self.frame is not None:
            yield from self.frame.push(chunk)
        else:
            if len(self.body) + len(chunk) > self.budget.body_bytes:
                self.budget.stop('http_body_limit')
            self.body.extend(chunk)

    def finish(self):
        if self.frame is not None:
            yield from self.frame.finish()
        else:
            if (self.media_type.endswith(('/json', '+json')) or b'\x00' in self.body[:4]
                    or _JSON_START.match(self.body) or self.body.startswith((b'\xff\xfe', b'\xfe\xff'))):
                self.budget.shape(self.body)
            if self.body:
                yield bytes(self.body)

    async def __aiter__(self):
        async for chunk in self.stream:
            for part in self.push(chunk):
                yield part
        for part in self.finish():
            yield part

    async def aclose(self):
        await self.stream.aclose()


class _SSEFrames:
    """Preserve wire delimiters; check each frame before the SDK sees it."""

    def __init__(self, budget):
        self.budget = budget
        self.frame = bytearray()
        self.data = bytearray()
        self.line_start = 0
        self.pending_cr = False
        self.first_line = True

    def _append(self, part):
        if len(self.frame) + len(part) > self.budget.sse_bytes:
            self.budget.stop('sse_event_size_limit')
        self.frame.extend(part)

    def _line(self, delimiter_bytes):
        end = len(self.frame) - delimiter_bytes
        start = self.line_start
        self.line_start = 0 if start == end else len(self.frame)
        if self.first_line and self.frame.startswith(_UTF8_BOM, start, end):
            start += len(_UTF8_BOM)
        self.first_line = False
        if start == end:
            if end:
                self.budget.event()
                self.budget.shape(self.data)
            frame = bytes(self.frame)
            self.frame.clear()
            self.data.clear()
            self.line_start = 0
            yield frame
        elif self.frame.startswith(b'data:', start, end):
            value_start = start + 5
            if self.frame.startswith(b' ', value_start, end):
                value_start += 1
            if self.data:
                self.data.extend(b'\n')
            self.data.extend(self.frame[value_start:end])

    def push(self, chunk):
        if not chunk:
            return
        pos = 0
        if self.pending_cr:
            self.pending_cr = False
            if chunk.startswith(b'\n'):
                self._append(b'\n')
                pos = 1
            yield from self._line(1 + pos)
        for match in _NEWLINE.finditer(chunk, pos):
            self._append(memoryview(chunk)[pos:match.end()])
            pos = match.end()
            if pos == len(chunk) and match.group() == b'\r':
                self.pending_cr = True
                return
            yield from self._line(match.end() - match.start())
        self._append(memoryview(chunk)[pos:])

    def finish(self):
        if self.pending_cr:
            yield from self._line(1)
        if self.frame:
            # No blank separator: pass the partial frame as-is on normal EOF.
            # Its bytes were still counted against the frame and session limits.
            yield bytes(self.frame)
