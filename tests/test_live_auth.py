"""Live authentication probes with the pinned SDK and an in-memory HTTP transport."""

import contextlib
import io
import importlib.util
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from windows_observer_agent.live_auth import AuthProbeError, main, probe

HAS_LIVE = all(importlib.util.find_spec(name) is not None for name in ('mcp', 'httpx2'))

if HAS_LIVE:
    import httpx2
    from mcp_types.version import LATEST_HANDSHAKE_VERSION


@unittest.skipUnless(HAS_LIVE, 'Requires the live extra')
class LiveAuthTests(unittest.IsolatedAsyncioTestCase):
    key = 'a' * 64

    def setUp(self):
        self.calls = []
        self.challenge = 'Bearer'
        self.rejected_session = False
        self.enforce_auth = True
        self.negative_status = 401
        self.positive_status = 200
        self.delete_status = 204
        self.tools = ['ControlStatus', 'Screenshot', 'Snapshot']
        self.client = httpx2.AsyncClient

    async def handler(self, request):
        if str(request.url) != 'http://127.0.0.1:8765/mcp':
            raise AssertionError('Unexpected probe target')
        auth = request.headers.get('Authorization')
        body = json.loads(request.content) if request.method == 'POST' else {}
        method = body.get('method', request.method)
        self.calls.append((method, auth is not None, auth == 'Bearer ' + self.key))
        if method == 'initialize':
            self.assertEqual(body['jsonrpc'], '2.0')
            self.assertIsInstance(body['id'], int)
            self.assertEqual(body['params']['protocolVersion'], LATEST_HANDSHAKE_VERSION)
            self.assertIn('clientInfo', body['params'])
            self.assertIn('capabilities', body['params'])
            self.assertIn('application/json', request.headers['Accept'])
            self.assertIn('text/event-stream', request.headers['Accept'])
        if self.enforce_auth and auth != 'Bearer ' + self.key:
            self.assertEqual(method, 'initialize')
            self.assertNotIn('Mcp-Session-Id', request.headers)
            headers = {'WWW-Authenticate': self.challenge, 'Location': 'http://example.invalid/'}
            if self.rejected_session:
                headers['Mcp-Session-Id'] = 'unexpected-session'
            return httpx2.Response(self.negative_status, headers=headers, text=self.key)
        if method == 'GET':
            return httpx2.Response(405)
        if method == 'DELETE':
            return httpx2.Response(self.delete_status)
        headers = {'Mcp-Session-Id': 'test-only-session'}
        if method == 'notifications/initialized':
            return httpx2.Response(202, headers=headers)
        if method == 'initialize':
            if self.positive_status != 200:
                return httpx2.Response(self.positive_status, text=self.key, headers=headers)
            result = {'protocolVersion': LATEST_HANDSHAKE_VERSION, 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'test-double', 'version': '0.8.8'}}
        elif method == 'tools/list':
            result = {'tools': [{'name': name, 'inputSchema': {'type': 'object'}} for name in self.tools]}
        else:
            raise AssertionError('Probe reached an unexpected MCP method')
        return httpx2.Response(200, headers=headers, json={'jsonrpc': '2.0', 'id': body['id'], 'result': result})

    @contextlib.contextmanager
    def offline(self):
        def client(**kwargs):
            self.assertIs(kwargs['trust_env'], False)
            self.assertIs(kwargs['follow_redirects'], False)
            return self.client(transport=httpx2.MockTransport(self.handler), **kwargs)
        with patch.dict(os.environ, {'WOA_MCP_TOKEN': self.key}), patch('httpx2.AsyncClient', client), \
             patch('socket.socket.connect', side_effect=AssertionError('Real network forbidden')):
            yield

    async def test_s5_missing_and_wrong_credentials_are_distinct_valid_requests(self):
        with self.offline():
            await probe(8765, 'missing')
            await probe(8765, 'wrong')
        self.assertEqual(self.calls, [('initialize', False, False), ('initialize', True, False)])

    async def test_s5_correct_credentials_initialize_and_list_without_calling_tools(self):
        with self.offline():
            await probe(8765, 'correct')
        methods = [m for m, _, _ in self.calls if m not in ('GET', 'DELETE')]
        self.assertEqual(methods, ['initialize', 'notifications/initialized', 'tools/list'])
        self.assertTrue(self.calls and all(present and correct for _, present, correct in self.calls))

    async def test_s5_unauthenticated_successful_handshake_is_a_failure(self):
        self.enforce_auth = False
        for mode in ('missing', 'wrong'):
            with self.subTest(mode=mode), self.offline():
                with self.assertRaisesRegex(AuthProbeError, 'HTTP 200'):
                    await probe(8765, mode)
        self.assertEqual(len(self.calls), 2)

    async def test_s5_redirect_wrong_route_and_server_errors_do_not_count_as_rejection(self):
        for status in (307, 403, 404, 500):
            self.negative_status = status
            self.calls.clear()
            with self.subTest(status=status), self.offline():
                with self.assertRaisesRegex(AuthProbeError, f'HTTP {status}') as raised:
                    await probe(8765, 'missing')
                self.assertTrue(self.key not in str(raised.exception), 'Probe error exposed credential')
                self.assertEqual(len(self.calls), 1)

    async def test_s5_401_requires_bearer_challenge_and_no_session(self):
        for challenge, session in (('Basic', False), ('Bearer', True)):
            self.challenge, self.rejected_session = challenge, session
            with self.subTest(challenge=challenge, session=session), self.offline():
                with self.assertRaises(AuthProbeError):
                    await probe(8765, 'wrong')

    async def test_s5_unexpected_close_status_prevents_acceptance(self):
        self.delete_status = 500
        with self.offline(), self.assertRaisesRegex(AuthProbeError, 'HTTP 500'):
            await probe(8765, 'correct')

    async def test_s5_correct_token_requires_exact_tool_set_without_duplicates(self):
        for tools in (['Snapshot'], self.tools + ['Click'], self.tools + ['Snapshot']):
            self.tools = tools
            with self.subTest(tools=tools), self.offline():
                with self.assertRaises(AuthProbeError):
                    await probe(8765, 'correct')

    async def test_s5_correct_token_reports_http_failures_without_echoing_body(self):
        for status in (201, 307, 401, 404, 500):
            self.positive_status = status
            with self.subTest(status=status), self.offline():
                with self.assertRaisesRegex(AuthProbeError, f'HTTP {status}') as raised:
                    await probe(8765, 'correct')
                self.assertTrue(self.key not in str(raised.exception), 'Probe error exposed credential')


@unittest.skipUnless(HAS_LIVE, 'Requires the live extra')
class AuthProbeCLITests(unittest.TestCase):
    def test_s5_connection_failure_is_safe_and_not_a_rejection(self):
        fake = 'TEST_ONLY_' + 'z' * 48
        failing = AsyncMock(side_effect=httpx2.ConnectError('Authorization: Bearer ' + fake))
        with patch('windows_observer_agent.live_auth.probe', failing), \
             contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(['8765', 'missing']), 2)
        report = out.getvalue() + err.getvalue()
        self.assertIn('LIVE_AUTH_FAILED', report)
        self.assertTrue(fake not in report and 'Authorization:' not in report, 'Probe error leaked a credential')
        self.assertNotIn('LIVE_AUTH_PROBE_PASS', report)
