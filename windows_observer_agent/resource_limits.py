"""Byte and JSON shape limits shared by untrusted input paths."""
import json
import os
import re
import stat

MIB = 1024 * 1024
MCP_HTTP_BYTES = MCP_EVENT_BYTES = LOCAL_JSON_BYTES = 16 * MIB
MCP_SESSION_BYTES = 4 * MCP_EVENT_BYTES
MCP_SSE_EVENTS = 1024
MCP_HTTP_REQUESTS = 128
MCP_TOOL_PAGE = 128
MCP_TOOLS_TOTAL = 512
MCP_TOOL_PAGES = 20
MODEL_TEXT_BYTES = 64 * 1024
MODEL_HTTP_BYTES = 16 * MODEL_TEXT_BYTES

JSON_DEPTH = 64
JSON_FIELDS = 1024
JSON_ITEMS = 16384
JSON_STRING_BYTES = MCP_HTTP_BYTES
JSON_NODES = 100_000

_JSON_TOKEN = re.compile(rb'"|[{}\[\],:]|[^\x20\t\r\n{}\[\],:"]+')
_JSON_STRING_END = re.compile(rb'[^"\\]*+(?:\\.[^"\\]*+)*+"', re.DOTALL)


class InputFileError(ValueError):
    """A required input could not be opened or is not a regular file."""


class ResourceLimitError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f'资源限制：{code}')


def check_json_shape(data: bytes | bytearray) -> None:
    """Check structural costs before constructing Python/SDK objects.

    Count opening containers and scalars (including keys) as nodes. The
    consumer's JSON parser still decides syntax and schema validity.
    """
    if b'\x00' in data:
        # Raw NUL cannot occur in UTF-8 JSON. Reject UTF-16/32 representations
        # so the lexical checks and the consumer see the same delimiters.
        raise ResourceLimitError('json_wire_encoding')
    stack = []
    pos = nodes = 0
    while (token := _JSON_TOKEN.search(data, pos)) is not None:
        start, pos = token.span()
        first = data[start]
        if first in (ord('}'), ord(']')):
            if stack:
                stack.pop()
            continue
        if first == ord(':'):
            if stack and stack[-1][0] == ord('{'):
                stack[-1][1] += 1
                if stack[-1][1] > JSON_FIELDS:
                    raise ResourceLimitError('json_fields_limit')
            continue
        if first == ord(','):
            continue
        nodes += 1
        if nodes > JSON_NODES:
            raise ResourceLimitError('json_nodes_limit')
        if stack and stack[-1][0] == ord('['):
            stack[-1][1] += 1
            if stack[-1][1] > JSON_ITEMS:
                raise ResourceLimitError('json_items_limit')
        if first in (ord('{'), ord('[')):
            if len(stack) == JSON_DEPTH:
                raise ResourceLimitError('json_depth_limit')
            stack.append([first, 0])
        elif first == ord('"'):
            end = _JSON_STRING_END.match(data, pos, pos + JSON_STRING_BYTES + 1)
            if end is None:
                if len(data) - pos > JSON_STRING_BYTES:
                    raise ResourceLimitError('json_string_limit')
                # Unterminated string: let the real parser report malformed JSON.
                break
            pos = end.end()


def load_bounded_json(text: str | bytes, **options):
    data = text.encode('utf-8') if isinstance(text, str) else text
    check_json_shape(data)
    return json.loads(text, **options)


def read_json_file(path, *, limit: int = LOCAL_JSON_BYTES):
    try:
        with path.open('rb') as source:
            status = os.fstat(source.fileno())
            if not stat.S_ISREG(status.st_mode):
                raise InputFileError('只接受普通 JSON 文件')
            if status.st_size > limit:
                raise ResourceLimitError('file_bytes_limit')
            data = source.read(limit + 1)
    except OSError:
        raise InputFileError('无法打开或读取 JSON 文件') from None
    if len(data) > limit:
        raise ResourceLimitError('file_bytes_limit')
    return load_bounded_json(data.decode('utf-8'))
