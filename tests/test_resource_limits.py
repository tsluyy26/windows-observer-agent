"""S6 boundaries with synthetic files and memory HTTP streams."""
from importlib.util import find_spec
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from windows_observer_agent.resource_limits import (
    JSON_DEPTH, JSON_FIELDS, JSON_ITEMS, LOCAL_JSON_BYTES, MODEL_TEXT_BYTES,
    MCP_EVENT_BYTES, ResourceLimitError, check_json_shape, load_bounded_json, read_json_file,
)

if find_spec('httpx2') is not None:
    import httpx2
    from windows_observer_agent.http_limits import HttpResponseBudget

    class CountedStream(httpx2.AsyncByteStream):
        def __init__(self, chunks):
            self.chunks = chunks
            self.reads = self.closed = 0

        async def __aiter__(self):
            for chunk in self.chunks:
                self.reads += 1
                yield chunk

        async def aclose(self):
            self.closed += 1


class LocalResourcesTests(unittest.TestCase):
    def test_s6_files_at_limit_and_one_byte_over(self):
        from windows_observer_agent.mcp_client import MockClient
        from windows_observer_agent.replay import ReplayClient
        from test_mcp import records

        inputs = ((MockClient, {'schema': 'woa.mock.v1', 'mock': True}),
                  (ReplayClient, {'schema': 'woa.replay.v1', 'redacted': True,
                   'records': [dict(record, redacted=True) for record in records()]}))
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'observation.json'
            for client, value in inputs:
                with self.subTest(client=client.__name__):
                    data = json.dumps(value).encode()
                    file.write_bytes(data + b' ' * (LOCAL_JSON_BYTES - len(data)))
                    self.assertIsNotNone(client(file).collect())
                    with file.open('ab') as handle:
                        handle.write(b' ')
                    with self.assertRaisesRegex(ResourceLimitError, 'file_bytes_limit'):
                        client(file).collect()

    def test_s6_file_growth_uses_same_handle_and_bounded_read(self):
        class GrowingFile(io.BytesIO):
            def fileno(self):
                return 123

            def read(self, count=-1):
                self.requested = count
                return super().read(count)

        source = GrowingFile(b' ' * 16 + b'GROWTH_SENTINEL')
        with patch.object(Path, 'open', return_value=source) as opened, \
             patch('windows_observer_agent.resource_limits.os.fstat',
                   return_value=SimpleNamespace(st_mode=0o100644, st_size=16)) as stat_handle:
            with self.assertRaisesRegex(ResourceLimitError, 'file_bytes_limit'):
                read_json_file(Path('synthetic.json'), limit=16)
        opened.assert_called_once_with('rb')
        stat_handle.assert_called_once_with(123)
        self.assertEqual(source.requested, 17)
        self.assertTrue(source.closed)

    def test_s6_missing_file_error_does_not_echo_path(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError) as caught:
                read_json_file(Path(directory) / 'S6_PRIVATE_PATH.json')
        self.assertTrue('S6_PRIVATE_PATH' not in str(caught.exception))

    def test_s6_json_limits_precede_object_construction(self):
        fixtures = (
            ('json_depth_limit', b'[' * (JSON_DEPTH + 1) + b']' * (JSON_DEPTH + 1)),
            ('json_items_limit', b'[' + b'0,' * JSON_ITEMS + b'0]'),
            ('json_fields_limit', b'{' + b'"key":0,' * JSON_FIELDS + b'"end":0}'),
        )
        for code, raw in fixtures:
            with self.subTest(code=code), patch('windows_observer_agent.resource_limits.json.loads') as parser:
                with self.assertRaisesRegex(ResourceLimitError, code):
                    load_bounded_json(raw)
                parser.assert_not_called()
        with patch('windows_observer_agent.resource_limits.JSON_STRING_BYTES', 6):
            check_json_shape(b'{"k":"a\\"b"}')
            with self.assertRaisesRegex(ResourceLimitError, 'json_string_limit'):
                check_json_shape(b'["1234567"]')
        with patch('windows_observer_agent.resource_limits.JSON_NODES', 4):
            with self.assertRaisesRegex(ResourceLimitError, 'json_nodes_limit'):
                check_json_shape(b'[[0],[1]]')

    def test_s6_escaped_punctuation_string_is_not_nesting(self):
        check_json_shape(json.dumps({'items': ['[{"' * 1000, '雪']}).encode())


@unittest.skipUnless(find_spec('httpx2'), 'Requires the locked HTTPX2 extra')
class HttpResourcesTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, budget, stream, headers, *, read=True):
        async def handler(request):
            return httpx2.Response(200, headers=headers, stream=stream)
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler),
                event_hooks={'request': [budget.guard_request], 'response': [budget.guard_response]}) as client:
            async with client.stream('GET', 'http://127.0.0.1/fixture') as response:
                if read:
                    return await response.aread()
                return [part async for part in response.aiter_raw()]

    async def test_s6_http_checks_actual_bytes_before_reading_trailing_chunks(self):
        exact = CountedStream([b'{', b'}'])
        self.assertEqual(await self.request(HttpResponseBudget(2), exact, {}), b'{}')
        self.assertEqual(exact.closed, 1)
        for headers in ({}, {'Content-Length': '1'}, {'Content-Length': 'invalid'}):
            stream = CountedStream([b'{}', b' ', b'S6_TRAILING_SENTINEL'])
            with self.subTest(headers=headers), self.assertRaisesRegex(ResourceLimitError, 'http_body_limit'):
                await self.request(HttpResponseBudget(2), stream, headers)
            self.assertEqual(stream.reads, 2)
            self.assertEqual(stream.closed, 1)

    async def test_s6_declared_oversize_and_compression_reject_before_first_read(self):
        for headers, code in (({'Content-Length': '999'}, 'http_body_limit'),
                              ({'Content-Encoding': 'gzip'}, 'http_encoding')):
            stream = CountedStream([b'S6_TRAILING_SENTINEL'])
            with self.subTest(code=code), self.assertRaisesRegex(ResourceLimitError, code):
                await self.request(HttpResponseBudget(32), stream, headers)
            self.assertEqual(stream.reads, 0)
            self.assertEqual(stream.closed, 1)

    async def test_s6_sse_event_at_and_over_production_limit(self):
        headers = {'Content-Type': 'text/event-stream'}
        prefix, suffix = b'data: "', b'"\n\n'
        data = b'x' * (MCP_EVENT_BYTES - len(prefix) - len(suffix))
        exact = CountedStream([prefix, data, suffix])
        parts = await self.request(HttpResponseBudget(32, sse_bytes=MCP_EVENT_BYTES), exact, headers, read=False)
        self.assertEqual(sum(map(len, parts)), MCP_EVENT_BYTES)
        stream = CountedStream([prefix, data, b'x' + suffix, b'S6_TRAILING_SENTINEL'])
        with self.assertRaisesRegex(ResourceLimitError, 'sse_event_size_limit'):
            await self.request(HttpResponseBudget(32, sse_bytes=MCP_EVENT_BYTES), stream, headers, read=False)
        self.assertEqual(stream.reads, 3)
        self.assertEqual(stream.closed, 1)

    async def test_s6_sse_total_survives_reconnect_and_blocks_further_requests(self):
        data = b'data: {}\n\n'
        budget = HttpResponseBudget(64, sse_bytes=32, total_bytes=2 * len(data))
        headers = {'Content-Type': 'text/event-stream'}
        for _ in range(2):
            await self.request(budget, CountedStream([data]), headers)
        stream = CountedStream([data, b'S6_TRAILING_SENTINEL'])
        with self.assertRaisesRegex(ResourceLimitError, 'http_session_limit'):
            await self.request(budget, stream, headers)
        self.assertEqual((stream.reads, stream.closed), (1, 1))
        blocked = CountedStream([data])
        with self.assertRaises(ResourceLimitError):
            await self.request(budget, blocked, headers)
        self.assertEqual((blocked.reads, blocked.closed), (0, 0))

    async def test_s6_sse_counts_small_events_and_preserves_fragmented_line_endings(self):
        budget = HttpResponseBudget(64, sse_bytes=32, sse_events=2)
        headers = {'Content-Type': 'text/event-stream'}
        stream = CountedStream([b'data: {}\r', b'\n\r', b'\ndata: {}\r\r'])
        parts = await self.request(budget, stream, headers, read=False)
        self.assertEqual(b''.join(parts), b'data: {}\r\n\r\ndata: {}\r\r')
        excess = CountedStream([b'data: {}\n\n', b'S6_TRAILING_SENTINEL'])
        with self.assertRaisesRegex(ResourceLimitError, 'sse_event_count_limit'):
            await self.request(budget, excess, headers)
        self.assertEqual((excess.reads, excess.closed), (1, 1))

    async def test_s6_bodyless_requests_cannot_bypass_session_limit(self):
        budget = HttpResponseBudget(64, requests=2)
        for _ in range(2):
            await self.request(budget, CountedStream([]), {})
        blocked = CountedStream([b'S6_TRAILING_SENTINEL'])
        with self.assertRaisesRegex(ResourceLimitError, 'http_request_count_limit'):
            await self.request(budget, blocked, {})
        self.assertEqual((blocked.reads, blocked.closed), (0, 0))

    async def test_s6_non_utf8_wire_json_is_rejected_before_sdk_parses(self):
        stream = CountedStream(['{"a":1}'.encode('utf-16')])
        with self.assertRaisesRegex(ResourceLimitError, 'json_wire_encoding'):
            await self.request(HttpResponseBudget(64), stream, {})
        self.assertEqual(stream.closed, 1)

    async def test_s6_sse_json_depth_is_checked_before_event_is_yielded(self):
        raw = b'data: ' + b'[' * (JSON_DEPTH + 1) + b']' * (JSON_DEPTH + 1) + b'\n\n'
        with self.assertRaisesRegex(ResourceLimitError, 'json_depth_limit'):
            await self.request(HttpResponseBudget(256, sse_bytes=256),
                               CountedStream([raw]), {'Content-Type': 'text/event-stream'})


@unittest.skipUnless(find_spec('mcp') and find_spec('httpx2'), 'Requires the locked MCP extra')
class McpResourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import os

        owner = self

        class MemoryClient(httpx2.AsyncClient):
            def __init__(self, **kwargs):
                super().__init__(transport=httpx2.MockTransport(owner.handler), **kwargs)

        self.enterContext(patch.dict(os.environ, {'WOA_MCP_TOKEN': 't' * 48}))
        self.enterContext(patch('httpx2.AsyncClient', MemoryClient))
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('Real network forbidden')))

    def make_client(self, handler):
        from windows_observer_agent.mcp_client import MCPClient
        from windows_observer_agent.transports import StreamableHTTPTransport

        self.handler = handler
        return MCPClient(StreamableHTTPTransport('http://127.0.0.1:8123/mcp', enabled=True), call_timeout=2)

    @staticmethod
    def init_reply(body, *, session=False):
        headers = {'Mcp-Session-Id': 'fixture-session'} if session else {}
        return httpx2.Response(200, headers=headers, json={
            'jsonrpc': '2.0', 'id': body['id'], 'result': {
                'protocolVersion': body['params']['protocolVersion'], 'capabilities': {'tools': {}},
                'serverInfo': {'name': 's6-fixture', 'version': '1'},
            }})

    async def test_s6_locked_mcp_sdk_cuts_off_streamed_json_before_parsing(self):
        from windows_observer_agent.mcp_client import MCPPolicyError
        stream = CountedStream([b'{' + b' ' * 511, b' ', b'S6_TRAILING_SENTINEL'])

        async def handler(request):
            return httpx2.Response(200, headers={'Content-Type': 'application/json', 'Content-Length': '1'},
                                   stream=stream)

        client = self.make_client(handler)
        with patch('windows_observer_agent.transports.MCP_HTTP_BYTES', 512):
            with self.assertRaisesRegex(MCPPolicyError, 'http_body_limit'):
                async with client:
                    self.fail('An oversized response initialized the client')
        self.assertEqual((stream.reads, stream.closed), (2, 1))

    async def test_s6_sdk_resumption_keeps_session_budget_and_closes_on_overflow(self):
        from windows_observer_agent.mcp_client import MCPPolicyError

        initial = b'id: 1\nretry: 1\ndata: {"jsonrpc":"2.0","method":"notifications/message","params":{"level":"info","data":"synthetic"}}\n\n'
        resumes = []
        first = CountedStream([initial])
        resumed = CountedStream([b': ' + b' ' * 540 + b'\n\n', b'S6_TRAILING_SENTINEL'])

        async def handler(request):
            if request.method == 'GET':
                if 'Last-Event-ID' not in request.headers:
                    return httpx2.Response(405)
                resumes.append(request.headers['Last-Event-ID'])
                return httpx2.Response(200, headers={'Content-Type': 'text/event-stream'}, stream=resumed)
            if request.method == 'DELETE':
                return httpx2.Response(204)
            body = json.loads(request.content)
            method = body['method']
            if method == 'initialize':
                return self.init_reply(body, session=True)
            if method == 'notifications/initialized':
                return httpx2.Response(202)
            self.assertEqual(method, 'tools/list')
            return httpx2.Response(200, headers={'Content-Type': 'text/event-stream'}, stream=first)

        client = self.make_client(handler)
        with patch('windows_observer_agent.transports.MCP_SESSION_BYTES', 768):
            with self.assertRaisesRegex(MCPPolicyError, 'http_session_limit'):
                async with client:
                    await client.list_tools()
        self.assertEqual(resumes, ['1'])
        self.assertEqual((first.closed, resumed.reads, resumed.closed), (1, 1, 1))

    async def test_s6_oversized_tool_response_is_bounded_even_without_content_length(self):
        from windows_observer_agent.mcp_client import MCPPolicyError
        oversized = CountedStream([b' ' * 513, b'S6_TRAILING_SENTINEL'])

        async def handler(request):
            body = json.loads(request.content)
            if body['method'] == 'initialize':
                return self.init_reply(body)
            if body['method'] == 'notifications/initialized':
                return httpx2.Response(202)
            self.assertEqual(body['method'], 'tools/call')
            self.assertEqual(body['params']['name'], 'Screenshot')
            return httpx2.Response(200, headers={'Content-Type': 'application/json'}, stream=oversized)

        client = self.make_client(handler)
        with patch('windows_observer_agent.transports.MCP_HTTP_BYTES', 512):
            async with client:
                with self.assertRaisesRegex(MCPPolicyError, 'http_body_limit'):
                    await client.call_tool('Screenshot')
        self.assertEqual((oversized.reads, oversized.closed), (1, 1))

    async def test_s6_stalled_sse_still_times_out_and_closes(self):
        import asyncio
        from windows_observer_agent.mcp_client import MCPTimeoutError

        class StalledStream(CountedStream):
            async def __aiter__(self):
                self.reads += 1
                yield b'data: {'
                await asyncio.Future()

        stalled = StalledStream([])

        async def handler(request):
            body = json.loads(request.content)
            if body['method'] == 'initialize':
                return self.init_reply(body)
            if body['method'] == 'notifications/initialized':
                return httpx2.Response(202)
            self.assertEqual(body['method'], 'tools/list')
            return httpx2.Response(200, headers={'Content-Type': 'text/event-stream'}, stream=stalled)

        client = self.make_client(handler)
        async with client:
            client.call_timeout = .1
            with self.assertRaises(MCPTimeoutError):
                await client.list_tools()
        self.assertEqual((stalled.reads, stalled.closed), (1, 1))

    async def test_s6_discovery_limits_and_duplicates_stop_before_more_pages(self):
        from windows_observer_agent.mcp_client import MCPError
        from windows_observer_agent.resource_limits import MCP_TOOL_PAGE, MCP_TOOL_PAGES

        def tool(name):
            return {'name': name, 'inputSchema': {'type': 'object'}}

        def page(index, size):
            return [tool(f'tool_{index}_{number}') for number in range(size)]

        cases = (
            ([{'tools': page(0, MCP_TOOL_PAGE + 1), 'nextCursor': 'x'}], 'mcp_tool_page_limit'),
            ([{'tools': page(i, MCP_TOOL_PAGE), 'nextCursor': str(i)} for i in range(5)],
             'mcp_tools_total_limit'),
            ([{'tools': page(i, 1), 'nextCursor': str(i)} for i in range(MCP_TOOL_PAGES)],
             'mcp_tool_pages_limit'),
            ([{'tools': [tool('A'), tool('A')], 'nextCursor': 'x'}], '重复'),
            ([{'tools': [tool('B')], 'nextCursor': 'again'},
              {'tools': [tool('C')], 'nextCursor': 'again'}], '重复'),
            ([{'tools': [{'name': [], 'inputSchema': {}}], 'nextCursor': 'x'}], '失败'),
        )
        for pages, pattern in cases:
            calls = []

            async def handler(request):
                if request.method == 'DELETE':
                    return httpx2.Response(204)
                body = json.loads(request.content)
                if body['method'] == 'initialize':
                    return self.init_reply(body)
                if body['method'] == 'notifications/initialized':
                    return httpx2.Response(202)
                self.assertEqual(body['method'], 'tools/list')
                result = pages[len(calls)]
                calls.append(body['id'])
                return httpx2.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'result': result})

            with self.subTest(pattern=pattern):
                client = self.make_client(handler)
                with self.assertRaisesRegex(MCPError, pattern):
                    async with client:
                        await client.list_tools()
                self.assertEqual(len(calls), len(pages))


@unittest.skipUnless(find_spec('openai') and find_spec('httpx2'), 'Requires the locked model extra')
class ModelResourceTests(unittest.TestCase):
    def run_model(self, handler):
        import os
        from contextlib import ExitStack
        from windows_observer_agent.llm_planner import plan_with_provider
        from windows_observer_agent.mcp_client import MockClient
        from windows_observer_agent.providers import ModelCapabilities, ProviderAccess, ProviderConfig, ProviderManager
        from windows_observer_agent.scene_model import build_scene

        class MemoryClient(httpx2.AsyncClient):
            def __init__(self, **kwargs):
                super().__init__(transport=httpx2.MockTransport(handler), **kwargs)

        config = ProviderConfig('resource-fixture', 'openai_chat_completions', 'fixture-model',
                                'https://example.invalid/v1', api_key_env='S6_MODEL_FIXTURE_KEY',
                                capabilities=ModelCapabilities(chat_completions_api=True))
        access = ProviderAccess(enabled=True, share_confirmed=True, confirmed_endpoint=config.base_url)
        manager = ProviderManager({config.name: config}, config.name, access=access)
        scene = build_scene(MockClient().collect())
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'S6_MODEL_FIXTURE_KEY': 's6_fixture_key_n5t3'}))
            stack.enter_context(patch('httpx2.AsyncClient', MemoryClient))
            stack.enter_context(patch('socket.getaddrinfo', side_effect=AssertionError('Real DNS forbidden')))
            return plan_with_provider('新建一份空白记事本文档', scene, manager)

    def test_s6_locked_model_sdk_does_not_read_past_limit_and_falls_back(self):
        from windows_observer_agent.resource_limits import MODEL_HTTP_BYTES

        content = CountedStream([b'{' + b' ' * (MODEL_HTTP_BYTES - 1), b' ', b'S6_TRAILING_SENTINEL'])

        async def handler(request):
            self.assertEqual(request.headers['Accept-Encoding'], 'identity')
            return httpx2.Response(200, headers={'Content-Type': 'application/json'}, stream=content)

        plan = self.run_model(handler)
        self.assertEqual(plan.fallback_reason, 'model_resource_limit')
        self.assertEqual((content.reads, content.closed), (2, 1))
        self.assertFalse(plan.executable)

    def test_s6_model_text_counts_utf8_at_exact_64_kib_and_one_byte_over(self):
        from windows_observer_agent.provider_adapters import ChatCompletionsAdapter
        from windows_observer_agent.providers import ModelFailure

        exact = '雪' * (MODEL_TEXT_BYTES // 3) + 'x' * (MODEL_TEXT_BYTES % 3)
        adapter = ChatCompletionsAdapter(SimpleNamespace(name='offline', model='synthetic'))
        self.assertEqual(adapter._result(exact, None, api_key='fake').text, exact)
        with self.assertRaisesRegex(ModelFailure, 'model_invalid_response'):
            adapter._result(exact + 'x', None, api_key='fake')
