"""Security regression: every error payload uses synthetic markers only."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from windows_observer_agent.__main__ import main
from windows_observer_agent.live import observe_live
from windows_observer_agent.live_auth import AuthProbeError, main as auth_main
from windows_observer_agent.mcp_client import MCPCallError, MCPClient, MCPConnectionError, MCPTimeoutError
from windows_observer_agent.resource_limits import ResourceLimitError
from test_live import LiveTransport


class PublicErrorTests(unittest.TestCase):
    def setUp(self):
        self.secret = 'S9_SYNTHETIC_' + secrets.token_hex(20)
        self.private_path = str(Path('C:/synthetic-private') / self.secret)
        self.payload = self.private_path + '\r\n\x1b[2K\u202e' + self.secret
        self.args = ['--live', '--endpoint', 'http://127.0.0.1:8765/mcp']

    def assert_safe(self, text):
        escaped = ''.join(f'\\u{ord(c):04x}' for c in self.secret)
        self.assertFalse(any(marker in text for marker in
                             (self.secret, escaped, 'synthetic-private', '\x1b', '\u202e',
                              'Traceback', 'Authorization:')), 'Public output leaked a fixture marker')

    def invoke(self, args, status=2, *, entry=main):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            result = entry(args)
        combined = out.getvalue() + err.getvalue()
        self.assert_safe(combined)
        self.assertEqual(result, status)
        return out.getvalue(), err.getvalue()

    def test_s9_bad_cli_argument_does_not_echo_value(self):
        self.assertIn('参数', self.invoke(['--planner', self.payload])[1])

    def test_s9_missing_local_input(self):
        for flag in ('--mock-file', '--replay-file', '--provider-config'):
            with self.subTest(flag=flag):
                options = ['--validate-provider-config'] if flag == '--provider-config' else []
                self.assertIn('读取', self.invoke([*options, flag, self.private_path])[1])

    def test_s9_malformed_json_and_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'synthetic.data'
            file.write_text('{' + self.payload, encoding='utf-8')
            for flag in ('--mock-file', '--replay-file'):
                self.assertIn('格式', self.invoke([flag, str(file)])[1])
            self.assertIn('配置', self.invoke(['--validate-provider-config', '--provider-config', str(file)])[1])

    def test_s9_wrapped_json_decode_does_not_include_filename_or_body(self):
        error = json.JSONDecodeError(self.payload, self.payload, 0)
        with patch('windows_observer_agent.mcp_client.read_json_file', side_effect=error):
            self.assertIn('格式', self.invoke(['--mock-file', 'synthetic.json'])[1])

    def test_s9_connection_timeout_and_resource_errors_use_fixed_messages(self):
        for error, clue in ((MCPConnectionError(self.payload), '连接'),
                            (MCPTimeoutError(self.payload), '超时'),
                            (ResourceLimitError(self.payload), '限额')):
            with self.subTest(category=clue), patch('windows_observer_agent.live.collect_live', side_effect=error):
                self.assertIn(clue, self.invoke(self.args)[1])

    def test_s9_unknown_exception_chain_and_sdk_logs_are_hidden(self):
        log_state = logging.root.manager.disable

        def fail(_):
            logging.getLogger('synthetic.sdk').critical(self.payload)
            try:
                raise OSError(self.payload)
            except OSError as exc:
                raise RuntimeError(self.payload) from exc

        with patch('windows_observer_agent.live.collect_live', side_effect=fail):
            self.assertIn('未知', self.invoke(self.args)[1])
        self.assertEqual(logging.root.manager.disable, log_state)

    def test_s9_report_serialization_exception_stays_inside_boundary(self):
        with patch('windows_observer_agent.__main__.render_report', side_effect=TypeError(self.payload)):
            self.assertIn('报告', self.invoke([])[1])

    def test_s9_mock_error_members_are_projected_in_text_and_json(self):
        data = {'mock': True, 'schema': 'woa.mock.v1',
                'snapshot': {'success': False, 'error': self.payload},
                'control_status': {'success': False, 'error': self.payload}}
        with patch('windows_observer_agent.mcp_client.read_json_file', return_value=data):
            text, _ = self.invoke(['--mock-file', self.private_path], 1)
            output, _ = self.invoke(['--mock-file', self.private_path, '--json'], 1)
        self.assertIn('错误', text)
        self.assertTrue(json.loads(output)['scene']['errors'])

    def test_s9_live_partial_error_members_are_projected_in_text_and_json(self):
        payload = self.payload

        class FailingTransport(LiveTransport):
            async def call_tool(self, name, arguments):
                if name == 'Screenshot':
                    raise MCPCallError(payload, diagnostics={'category': payload, 'control_code': payload,
                                                             'content_types': ['text', payload], 'raw': payload})
                return await super().call_tool(name, arguments)

        import asyncio
        raw = asyncio.run(observe_live(MCPClient(FailingTransport())))
        with patch('windows_observer_agent.live.collect_live', return_value=raw):
            for options in ([], ['--json']):
                self.assertIn('Screenshot', self.invoke(self.args + options, 1)[0])
            report, error = self.invoke(self.args + ['--manual-summary'], 1)
        self.assertEqual(report.strip(), 'WOA_REPORT incomplete ready')
        self.assertEqual(error.strip(), 'WOA_ERROR live mcp_tool')

    def test_s9_auth_probe_does_not_echo_wrapped_exception(self):
        failure = AsyncMock(side_effect=AuthProbeError(self.payload))
        with patch('windows_observer_agent.live_auth.probe', failure):
            out, err = self.invoke(['8765', 'correct'], entry=auth_main)
        self.assertIn('LIVE_AUTH_FAILED [correct]', out + err)

    def test_s9_provider_policy_does_not_emit_raw_config_failure(self):
        from windows_observer_agent.providers import ModelPolicyError
        with patch('windows_observer_agent.llm_provider.OpenAIProvider.validate',
                   side_effect=ModelPolicyError(self.payload)):
            self.assertIn('共享', self.invoke(['--planner', 'openai', '--model', 'synthetic',
                                             '--confirm-data-sharing'])[1])

    @unittest.skipUnless(importlib.util.find_spec('openai'), '需安装 llm extra')
    def test_s9_provider_http_errors_leave_only_fallback_codes(self):
        import httpx2

        key, body = self.secret, self.payload
        base_client = httpx2.AsyncClient
        original_connect = socket.socket.connect

        def loopback_only(sock, address):
            # Windows asyncio uses a loopback socketpair internally.
            if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1'):
                raise AssertionError('Unexpected network request')
            return original_connect(sock, address)

        for status, category in ((401, 'model_authentication'), (500, 'model_failure')):
            with self.subTest(status=status):
                requests = []

                async def service(request):
                    requests.append(request)
                    return httpx2.Response(status, json={'error': {'message': body}})

                class MemoryClient(base_client):
                    def __init__(self, **options):
                        super().__init__(transport=httpx2.MockTransport(service), **options)

                args = ['--planner', 'openai', '--model', 'synthetic', '--confirm-data-sharing']
                with patch.dict(os.environ, {'WOA_LLM_API_KEY': key}, clear=True), \
                     patch('httpx2.AsyncClient', MemoryClient), \
                     patch('socket.socket.connect', loopback_only):
                    text, _ = self.invoke(args, 0)
                    output, _ = self.invoke(args + ['--json'], 0)
                self.assertIn(category, text)
                plan = json.loads(output)['plan']
                self.assertEqual(plan['fallback_reason'], category)
                self.assertFalse(plan['executable'])
                self.assertEqual(len(requests), 2)

    def test_s9_cancel_preserves_exit_status(self):
        with patch('windows_observer_agent.live.collect_live', side_effect=KeyboardInterrupt()):
            self.assertIn('取消', self.invoke(self.args, 130)[1])
