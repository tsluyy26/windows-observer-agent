"""真实、锁定的 SDK + httpx2.MockTransport；没有监听端口或桌面服务。"""
import asyncio
import contextlib
import importlib.util
import json
import os
import unittest
from unittest.mock import patch

from test_mcp import SNAPSHOT
from windows_observer_agent.mcp_client import (MCPAuthenticationError, MCPClient,
    MCPConnectionError, MCPPolicyError, MCPTimeoutError)
from windows_observer_agent.transports import StreamableHTTPTransport

HAS_SDK = importlib.util.find_spec('mcp') is not None


@unittest.skipUnless(HAS_SDK, '需 uv sync --extra live --locked 运行真实 SDK 合约测试')
class SDKHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx2
        self.httpx = httpx2
        self.requests = []
        self.clients = []
        self.auth_failure = False
        self.delay = 0
        self.pages = False
        self.fail_call = False
        self.redirect = False
        self.fail_discovery = False
        self.sse_response = False
        self.image_data = 'dGVzdA=='
        self.actual_client = httpx2.AsyncClient

    async def handler(self, request):
        self.assertEqual(request.url.host, '127.0.0.1')
        self.assertEqual(request.headers['Authorization'], 'Bearer ' + 't' * 48)
        if self.auth_failure:
            return self.httpx.Response(401, text='SECRET server error')
        if self.redirect:
            return self.httpx.Response(307, headers={'Location': 'http://example.com/leak'})
        if request.method == 'GET':
            return self.httpx.Response(405)
        if request.method == 'DELETE':
            self.requests.append('DELETE')
            return self.httpx.Response(204)
        body = json.loads(request.content)
        method = body['method']
        self.requests.append(method)
        if method.startswith('notifications/'):
            return self.httpx.Response(202)
        if method == 'initialize':
            if self.delay:
                await asyncio.sleep(self.delay)
            result = {'protocolVersion': body['params']['protocolVersion'],
                      'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'windows-mcp-test-double', 'version': '0.8.8'}}
        elif method == 'tools/list':
            if self.fail_discovery:
                raise self.httpx.ConnectError('SECRET discovery failure', request=request)
            cursor = body.get('params', {}).get('cursor')
            names = ['ControlStatus', 'Screenshot', 'Snapshot', 'Click']
            if self.pages and cursor is None:
                names = names[:2]
            elif self.pages:
                names = names[2:]
            result = {'tools': [{'name': name, 'inputSchema': {'type': 'object'}} for name in names]}
            if self.pages and cursor is None:
                result['nextCursor'] = 'page2'
        elif method == 'tools/call':
            name = body['params']['name']
            self.requests.append(name)
            if self.fail_call:
                result = {'isError': True, 'content': [{'type': 'text', 'text': 'SECRET tool error'}]}
            elif name == 'ControlStatus':
                status = {'state': 'ready', 'generation': 1, 'wait_seconds': 0}
                result = {'content': [{'type': 'text', 'text': json.dumps(status)}], 'structuredContent': status}
            elif name == 'Screenshot':
                result = {'content': [{'type': 'text', 'text': 'Screenshot Size: (1,1)'},
                    {'type': 'image', 'mimeType': 'image/png', 'data': self.image_data}]}
            else:
                result = {'content': [{'type': 'text', 'text': json.dumps([SNAPSHOT])}]}
        else:
            self.fail('Unexpected request: ' + method)
        packet = {'jsonrpc': '2.0', 'id': body['id'], 'result': result}
        if self.sse_response:
            return self.httpx.Response(200, content=('event: message\ndata: ' + json.dumps(packet) + '\n\n').encode(),
                headers={'Mcp-Session-Id': 'test-session', 'Content-Type': 'text/event-stream'})
        return self.httpx.Response(200, json=packet,
                                    headers={'Mcp-Session-Id': 'test-session'})

    def factory(self, **kwargs):
        self.assertFalse(kwargs['trust_env'])
        self.assertFalse(kwargs['follow_redirects'])
        client = self.actual_client(transport=self.httpx.MockTransport(self.handler), **kwargs)
        self.clients.append(client)
        return client

    @contextlib.contextmanager
    def isolated(self):
        with patch.dict(os.environ, {'WOA_MCP_TOKEN': 't' * 48}), \
             patch('httpx2.AsyncClient', self.factory), \
             patch('socket.socket.connect', side_effect=AssertionError('Real socket forbidden')):
            yield

    async def test_real_sdk_initialize_discovery_images_and_delete(self):
        from windows_observer_agent.live import observe_live
        self.pages = True
        with self.isolated():
            raw = await observe_live(MCPClient(StreamableHTTPTransport('http://127.0.0.1:8123/mcp', enabled=True)))
        self.assertTrue(raw.metadata['observation_complete'])
        self.assertIn('notifications/initialized', self.requests)
        self.assertEqual(self.requests.count('tools/list'), 2)
        self.assertEqual([x for x in self.requests if x in ('ControlStatus','Screenshot','Snapshot','Click')],
                         ['ControlStatus', 'Screenshot', 'Snapshot'])
        self.assertIn('DELETE', self.requests)
        self.assertTrue(all(client.is_closed for client in self.clients))

    async def test_sse_image_above_sdk_default_one_megabyte(self):
        import base64
        from windows_observer_agent.live import observe_live
        self.sse_response = True
        # 纯合成内容，不是截图。大于 SDK 默认的 1MiB SSE 上限。
        self.image_data = base64.b64encode(b'x' * (1024 * 1024)).decode('ascii')
        with self.isolated():
            raw = await observe_live(MCPClient(StreamableHTTPTransport('http://127.0.0.1:8123/mcp', enabled=True), call_timeout=2))
        self.assertTrue(raw.metadata['observation_complete'], raw.metadata)
        self.assertNotIn(self.image_data, repr(raw))

    async def test_real_sdk_auth_failure_redacted_and_closed(self):
        self.auth_failure = True
        with self.isolated(), self.assertRaises(MCPAuthenticationError) as caught:
            async with MCPClient(StreamableHTTPTransport('http://127.0.0.1:8123/mcp', enabled=True)):
                self.fail('Cannot authenticate')
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertTrue(all(client.is_closed for client in self.clients))

    async def test_real_sdk_initialization_timeout_cleans_partial_resources(self):
        self.delay = 10
        with self.isolated(), self.assertRaises(MCPTimeoutError):
            async with MCPClient(StreamableHTTPTransport('http://127.0.0.1:8123/mcp', enabled=True), connect_timeout=.03):
                self.fail('Must time out')
        self.assertTrue(all(client.is_closed for client in self.clients))

    async def test_real_sdk_http_failure_is_safe(self):
        async def broken(request):
            raise self.httpx.ConnectError('SECRET HTTP failure', request=request)
        self.handler = broken
        with self.isolated(), self.assertRaises(MCPConnectionError) as caught:
            async with MCPClient(StreamableHTTPTransport('http://127.0.0.1:8123/mcp', enabled=True)):
                pass
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertTrue(all(client.is_closed for client in self.clients))

    async def test_real_sdk_tool_failure_does_not_retry(self):
        from windows_observer_agent.live import observe_live
        self.fail_call = True
        with self.isolated():
            raw = await observe_live(MCPClient(StreamableHTTPTransport('http://127.0.0.1:8123/mcp', enabled=True)))
        self.assertFalse(raw.metadata['observation_complete'])
        self.assertEqual(self.requests.count('tools/call'), 1)
        self.assertNotIn('SECRET', repr(raw))

    async def test_redirect_never_sends_token_to_other_host(self):
        self.redirect = True
        with self.isolated(), self.assertRaises(MCPPolicyError):
            async with MCPClient(StreamableHTTPTransport('http://127.0.0.1:8123/mcp', enabled=True)):
                pass
        self.assertTrue(all(client.is_closed for client in self.clients))

    async def test_discovery_http_failure_cleans_and_reports(self):
        from windows_observer_agent.live import observe_live
        self.fail_discovery = True
        with self.isolated(), self.assertRaises(MCPConnectionError):
            await observe_live(MCPClient(StreamableHTTPTransport('http://127.0.0.1:8123/mcp', enabled=True)))
        self.assertTrue(all(client.is_closed for client in self.clients))

    async def test_external_cancel_during_handshake_preserves_cancel(self):
        self.delay = 10
        async def work():
            async with MCPClient(StreamableHTTPTransport('http://127.0.0.1:8123/mcp', enabled=True)):
                pass
        with self.isolated():
            task = asyncio.create_task(work())
            for _ in range(100):
                if 'initialize' in self.requests:
                    break
                await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(all(client.is_closed for client in self.clients))

    async def test_default_port_is_canonicalized_without_rejection(self):
        transport = StreamableHTTPTransport('http://127.0.0.1:80/mcp')
        request = self.httpx.Request('POST', 'http://127.0.0.1/mcp')
        await transport._guard_request(request)


if __name__ == '__main__':
    unittest.main()
