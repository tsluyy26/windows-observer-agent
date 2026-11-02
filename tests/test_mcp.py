"""通信合约测试：所有 transport 为内存对象，不监听端口。"""
import asyncio
import contextlib
from copy import deepcopy
import importlib
import io
import json
from pathlib import Path
import sys
import subprocess
import types
import unittest
from unittest.mock import patch

SNAPSHOT = '''Cursor Position: (1, 2)
Visible Displays: 0:DISPLAY (0,0,1920,1080) primary
Active Desktop:
Name
--------
Desktop 1

All Desktops:
Name
--------
Desktop 1

Focused Window:
Name        Depth  Status      Width  Height  Handle
----------  -----  ---------  ------  ------  ------
PrivateDoc      0  Normal       1200     800     100

Opened Windows:
Name        Depth  Status      Width  Height  Handle
----------  -----  ---------  ------  ------  ------
PrivateDoc      0  Normal       1200     800     100

UI Tree:
desktop
└── window "PrivateDoc"
    ├── (10,20) button "PrivateButton"  [action: click]
    └── (30,40) edit "PrivateText"  [action: fill] [value:"secret-value"]
'''


def envelope(text=SNAPSHOT):
    return {'content': [{'type': 'text', 'text': text}], 'isError': False}


def records():
    return [
        {'tool': 'ControlStatus', 'arguments': {}, 'observed_at': '2026-11-01T10:00:00+00:00',
         'result': {'content': [{'type': 'text', 'text': '{"state":"ready","generation":1,"wait_seconds":0}'}],
                    'structuredContent': {'state': 'ready', 'generation': 1, 'wait_seconds': 0}}},
        {'tool': 'Snapshot', 'arguments': {'use_vision': False},
         'observed_at': '2026-11-01T10:00:01+00:00', 'result': envelope()},
    ]


class ParsingTests(unittest.TestCase):
    def setUp(self):
        self.adapter = importlib.import_module('windows_observer_agent.mcp_adapter')

    def test_true_text_shape_to_scene(self):
        from windows_observer_agent.scene_model import build_scene
        raw = self.adapter.adapt_records(records(), source='Replay:test', kind='replay')
        scene = build_scene(raw)
        self.assertFalse(scene.raw.mock)
        self.assertEqual(scene.active_window.id, '100')
        self.assertIsNone(scene.active_window.app)  # title does not prove executable
        self.assertEqual(scene.monitors[0].width, 1920)
        self.assertEqual(scene.monitors[0].height, 1080)
        self.assertEqual(len(scene.active_window.controls), 2)
        self.assertIn('/result/content/0', scene.active_window.source)
        self.assertEqual(scene.control.owner, 'ready')
        self.assertFalse(scene.control.ready)
        self.assertTrue(scene.ui_tree_truncated)  # no proof of completeness
        self.assertTrue(scene.raw.tool_results)

    def test_mixed_blocks_strip_image_data_preserve_unknown(self):
        raw = envelope()
        raw['content'] += [{'type': 'image', 'mimeType': 'image/png', 'data': 'SECRET_IMAGE'},
                           {'type': 'future', 'extra': 42}]
        raw['future_field'] = {'extra': 42}
        result = self.adapter.normalize_result(raw)
        self.assertNotIn('SECRET_IMAGE', json.dumps(result))
        self.assertTrue(result['content'][1]['omitted'])
        self.assertEqual(result['future_field'], {'extra': 42})

    def test_fastmcp_serialized_list_text(self):
        data = records()
        data[1]['result'] = envelope(json.dumps([SNAPSHOT]))
        raw = self.adapter.adapt_records(data, source='Replay:test', kind='replay')
        self.assertTrue(raw.snapshot['success'])
        self.assertEqual(raw.snapshot['active_window_id'], '100')
        with self.assertRaises(ValueError):
            self.adapter.normalize_result(envelope(json.dumps(['Error capturing screenshot: failure'])))

    def test_tool_and_protocol_and_plain_text_errors(self):
        for payload in ({'error': {'code': -32603, 'message': 'failed'}},
                        {'content': [], 'isError': True},
                        envelope('Error capturing desktop state: failure. Please try again.'),
                        {'content': None}, {'content': [], 'isError': 'false'}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.adapter.normalize_result(payload)

    def test_truncation_marker_is_not_fixed_to_500(self):
        data = records()
        data[1]['result'] = envelope(SNAPSHOT + '\n... [truncated: reached the 20-element capture limit — some elements were not visited.]')
        raw = self.adapter.adapt_records(data, source='Replay:test', kind='replay')
        self.assertTrue(raw.snapshot['ui_tree_truncated'])
        self.assertEqual(raw.snapshot['capture_limit'], 20)

    def test_missing_and_unknown_fields_fail_conservatively(self):
        data = records()
        data[1]['result'] = envelope('Unexpected future format')
        raw = self.adapter.adapt_records(data, source='Replay:test', kind='replay')
        self.assertFalse(raw.snapshot['success'])
        self.assertTrue(raw.warnings)

    def test_control_variants_and_conflicts(self):
        for state in ('ready', 'user', 'takeover_pending', 'unavailable', 'mystery'):
            with self.subTest(state=state):
                result = self.adapter.parse_control({'structuredContent': {'state': state}})
                self.assertNotEqual(result['owner'], 'agent')
        self.assertEqual(self.adapter.parse_control({'structuredContent': {'state': 'ai', 'generation': 1, 'wait_seconds': 0}})['owner'], 'agent')
        conflict = {'structuredContent': {'state': 'ai'}, 'content': [{'type': 'text', 'text': '{"state":"user"}'}]}
        self.assertFalse(self.adapter.parse_control(conflict)['success'])
        self.assertFalse(self.adapter.parse_control({'structuredContent': {'state': 'ai', 'wait_seconds': -1}})['success'])

    def test_redaction_drops_unknown_text_and_structured_secrets(self):
        from windows_observer_agent.replay import sanitize_record
        rec = records()[1]
        rec['result']['content'].append({'type': 'image', 'mimeType': 'image/png', 'data': 'IMAGE_SECRET'})
        rec['result']['structuredContent'] = {'credential': 'TOKEN_SECRET'}
        rec['result']['unknown'] = 'OTHER_SECRET'
        rec['result']['content'][0]['text'] += '\nDO_NOT_SAVE_THIS\n'
        safe = sanitize_record(rec)
        encoded = json.dumps(safe)
        for secret in ('PrivateDoc', 'PrivateButton', 'PrivateText', 'secret-value',
                       'IMAGE_SECRET', 'TOKEN_SECRET', 'OTHER_SECRET', 'DO_NOT_SAVE_THIS'):
            self.assertNotIn(secret, encoded)
        self.assertTrue(safe['redacted'])
        self.assertIn('UI Tree:', safe['result']['content'][0]['text'])

    def test_unmatched_window_controls_preserved(self):
        from windows_observer_agent.scene_model import build_scene
        data = records()
        data[1]['result'] = envelope(SNAPSHOT.replace('window "PrivateDoc"', 'window "Other"'))
        scene = build_scene(self.adapter.adapt_records(data, source='Replay:test', kind='replay'))
        self.assertEqual(len(scene.unassigned_controls), 2)
        self.assertEqual(len(scene.active_window.controls), 0)

    def test_real_sanitized_replay_and_cli(self):
        from windows_observer_agent.replay import ReplayClient
        from windows_observer_agent.scene_model import build_scene
        from windows_observer_agent.__main__ import main
        path = Path(__file__).parent / 'fixtures' / 'windows_mcp_0_8_8_redacted.json'
        scene = build_scene(ReplayClient(path).collect())
        self.assertEqual(scene.raw.kind, 'replay')
        self.assertFalse(scene.raw.mock)
        # 最终采样在 user 状态下 Snapshot/Screenshot 返回错误；回放应保守报告失败。
        self.assertFalse(scene.observation_success)
        self.assertTrue(scene.errors)
        self.assertEqual(scene.control.owner, 'user')
        self.assertEqual(len(scene.raw.tool_results), 3)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(['--replay-file', str(path), '--json']), 1)
        self.assertEqual(json.loads(out.getvalue())['scene']['raw']['kind'], 'replay')

    def test_offline_modes_first_import_in_fresh_interpreter(self):
        # 子进程仅为测试宿主；审计钩子在应用首次导入前安装。
        script = '''
import sys
def audit(event, args):
    if event.startswith(('subprocess.', 'socket.')) or event in {'os.system','os.spawn','os.startfile','os.posix_spawn','os.exec'}:
        raise RuntimeError('offline side effect: ' + event)
sys.addaudithook(audit)
from windows_observer_agent.__main__ import main
assert main([]) == 0
assert main(['--replay-file', 'tests/fixtures/windows_mcp_0_8_8_redacted.json']) == 1
assert 'mcp' not in sys.modules
print('OFFLINE_AUDIT_PASS')
'''
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('OFFLINE_AUDIT_PASS', result.stdout)

    def test_historical_ai_does_not_grant_current_control(self):
        from windows_observer_agent.scene_model import build_scene
        data = records()
        data[0]['result'] = {'structuredContent': {'state': 'ai', 'generation': 1, 'wait_seconds': 0}}
        scene = build_scene(self.adapter.adapt_records(data, source='Replay:test', kind='replay'))
        self.assertEqual(scene.control.owner, 'agent')
        self.assertFalse(scene.control.ready)

    def test_control_error_object_is_not_ignored(self):
        from windows_observer_agent.control_gate import check_control
        self.assertFalse(check_control({'owner': 'agent', 'success': True, 'error': {'code': 1}}, 'test').ready)

    def test_recorded_tool_error_becomes_failed_scene(self):
        data = records()
        data[1]['result'] = {'isError': True, 'content': [{'type': 'text', 'text': 'PRIVATE_ERROR'}]}
        raw = self.adapter.adapt_records(data, source='Replay:test', kind='replay')
        self.assertFalse(raw.snapshot['success'])
        self.assertNotIn('PRIVATE_ERROR', repr(raw))

    def test_redaction_validates_generation_for_all_states(self):
        from windows_observer_agent.replay import sanitize_record
        record = records()[0]
        record['result'] = {'structuredContent': {'state': 'ready', 'generation': {'token': 'SECRET'}, 'wait_seconds': 0}}
        self.assertNotIn('SECRET', json.dumps(sanitize_record(record)))

    def test_redaction_aliases_are_consistent_across_text_blocks(self):
        from windows_observer_agent.replay import sanitize_record
        data = records()
        data[1]['result']['content'] = [
            {'type': 'text', 'text': SNAPSHOT.split('Opened Windows:')[0]},
            {'type': 'text', 'text': 'Opened Windows:\nOtherWindow  0  Normal  600  400  200\nPrivateDoc  0  Normal  1200  800  100\n'},
            {'type': 'text', 'text': 'UI Tree:' + SNAPSHOT.split('UI Tree:')[1]}]
        safe = sanitize_record(data[1])
        data[1] = safe
        raw = self.adapter.adapt_records(data, source='Replay:test', kind='replay')
        self.assertEqual(len(raw.snapshot['unassigned_controls']), 0)
        active = next(w for w in raw.snapshot['windows'] if w['id'] == raw.snapshot['active_window_id'])
        self.assertEqual(active['title'], 'redacted-window-1')
        self.assertEqual(active['properties']['width'], 1200)
        self.assertEqual(len(active['controls']), 2)

    def test_failed_replay_reports_failure_without_crashing(self):
        from windows_observer_agent.replay import ReplayClient
        from windows_observer_agent.scene_model import build_scene
        data = {'schema': 'woa.replay.v1', 'redacted': True, 'records': records()}
        for record in data['records']:
            record['redacted'] = True
        data['records'][1]['result'] = {'isError': True, 'content': []}
        with patch.object(Path, 'read_text', return_value=json.dumps(data)):
            scene = build_scene(ReplayClient(Path('unused.json')).collect())
        self.assertFalse(scene.observation_success)
        self.assertTrue(scene.errors)

    def test_sdk_snake_case_and_json_rpc_result(self):
        result = self.adapter.normalize_result({'jsonrpc': '2.0', 'id': 1, 'result': {
            'is_error': False, 'structured_content': {'state': 'user'}, 'content': []}})
        self.assertEqual(result['structuredContent']['state'], 'user')

    def test_multiple_text_blocks_keep_correct_source(self):
        data = records()
        data[1]['result']['content'] = [
            {'type': 'text', 'text': SNAPSHOT.split('UI Tree:')[0]},
            {'type': 'text', 'text': 'UI Tree:' + SNAPSHOT.split('UI Tree:')[1]}]
        raw = self.adapter.adapt_records(data, source='Replay:test', kind='replay')
        control = raw.snapshot['windows'][0]['controls'][0]
        self.assertIn('/content/1/', control['_source'])


class FakeTransport:
    def __init__(self, *, connect_delay=0, call_delay=0, fail=False):
        self.connect_delay, self.call_delay, self.fail = connect_delay, call_delay, fail
        self.closed = False
        self.calls = []

    async def connect(self):
        await asyncio.sleep(self.connect_delay)

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        await asyncio.sleep(self.call_delay)
        if self.fail:
            raise OSError('private transport details')
        return envelope('{}')

    async def close(self):
        self.closed = True


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clients = importlib.import_module('windows_observer_agent.mcp_client')
        self.transports = importlib.import_module('windows_observer_agent.transports')

    async def test_connect_timeout_closes_transport(self):
        transport = FakeTransport(connect_delay=1)
        with self.assertRaises(self.clients.MCPTimeoutError):
            async with self.clients.MCPClient(transport, connect_timeout=.01):
                self.fail('must not connect')
        self.assertTrue(transport.closed)

    async def test_call_timeout_closes_on_context_exit(self):
        transport = FakeTransport(call_delay=1)
        with self.assertRaises(self.clients.MCPTimeoutError):
            async with self.clients.MCPClient(transport, call_timeout=.01) as client:
                await client.call_tool('Snapshot', {})
        self.assertTrue(transport.closed)

    async def test_call_failure_is_typed_and_does_not_expose_details(self):
        transport = FakeTransport(fail=True)
        async with self.clients.MCPClient(transport) as client:
            with self.assertRaises(self.clients.MCPCallError) as result:
                await client.call_tool('Snapshot', {})
            self.assertNotIn('private', str(result.exception))
        self.assertTrue(transport.closed)

    async def test_whitelist_before_transport_call(self):
        transport = FakeTransport()
        async with self.clients.MCPClient(transport) as client:
            for name in ('Click', 'Shell', 'Control', 'snapshot'):
                with self.subTest(name=name), self.assertRaises(self.clients.MCPPolicyError):
                    await client.call_tool(name, {})
            with self.assertRaises(self.clients.MCPPolicyError):
                await client.call_tool('ControlStatus', {'takeover': True})
            for arguments in ({'use_vision': 'SECRET'}, {'region': 'SECRET'},
                              {'display': [True]}, {'region': [0, 0, 0, 0]}):
                with self.subTest(arguments=arguments), self.assertRaises(self.clients.MCPPolicyError):
                    await client.call_tool('Snapshot', arguments)
        self.assertEqual(transport.calls, [])

    async def test_use_before_connect_and_after_close(self):
        client = self.clients.MCPClient(FakeTransport())
        with self.assertRaises(self.clients.MCPConnectionError):
            await client.call_tool('Snapshot', {})
        async with client:
            await client.call_tool('Snapshot', {})
        with self.assertRaises(self.clients.MCPConnectionError):
            await client.call_tool('Snapshot', {})

    async def test_http_disabled_before_import_or_socket(self):
        transport = self.transports.StreamableHTTPTransport('http://127.0.0.1:8000/mcp')
        with self.assertRaises(self.clients.MCPPolicyError):
            await transport.connect()

    async def test_http_rejects_unconfirmed_remote_and_credentials(self):
        for url in ('https://example.com/mcp', 'http://user:pass@127.0.0.1/mcp', 'file:///x'):
            with self.subTest(url=url), self.assertRaises(self.clients.MCPPolicyError):
                self.transports.StreamableHTTPTransport(url, enabled=True)

    async def test_cancellation_cleans_connection(self):
        transport = FakeTransport(connect_delay=10)
        client = self.clients.MCPClient(transport)
        task = asyncio.create_task(client.__aenter__())
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(transport.closed)

    async def test_replay_order_and_arguments_checked(self):
        transport = self.transports.ReplayTransport(records())
        async with self.clients.MCPClient(transport) as client:
            with self.assertRaises(self.clients.MCPCallError):
                await client.call_tool('Snapshot', {})

    async def test_all_readonly_tools_and_replay_lifecycle(self):
        transport = self.transports.ReplayTransport(records())
        async with self.clients.MCPClient(transport) as client:
            listing = await client.list_tools()
            self.assertEqual({tool['name'] for tool in listing['tools']}, {'ControlStatus', 'Snapshot'})
            await client.call_tool('ControlStatus', {})
            result = await client.call_tool('Snapshot', {'use_vision': False})
            self.assertIn('content', result)
        self.assertFalse(transport.connected)

    async def test_observer_sequences_tools_with_receipt_times(self):
        from windows_observer_agent.observer import observe_mcp
        transport = FakeTransport()
        observation = await observe_mcp(self.clients.MCPClient(transport))
        self.assertEqual(transport.calls, ['ControlStatus', 'Snapshot'])
        self.assertEqual(observation.kind, 'mcp')
        self.assertTrue(all(r['observed_at'].endswith('+00:00') for r in observation.tool_results))
        self.assertTrue(transport.closed)

    async def test_invalid_timeout_configuration(self):
        for timeout in (0, -1, float('inf'), float('nan'), True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.clients.MCPClient(FakeTransport(), connect_timeout=timeout)

    async def test_close_timeout(self):
        class SlowClose(FakeTransport):
            async def close(self):
                await asyncio.sleep(1)
        client = self.clients.MCPClient(SlowClose(), close_timeout=.01)
        with self.assertRaises(self.clients.MCPTimeoutError):
            async with client:
                pass
        self.assertFalse(client.connected)

    async def test_http_sdk_lifecycle_without_network(self):
        events = []
        class FakeSession:
            def __init__(self, read, write): pass
            async def __aenter__(self):
                events.append('session-open')
                return self
            async def initialize(self): events.append('initialize')
            async def call_tool(self, name, arguments):
                return types.SimpleNamespace(model_dump=lambda **kw: envelope('{}'))
            async def __aexit__(self, *args): events.append('session-close')
        @contextlib.asynccontextmanager
        async def stream(url, **kwargs):
            self.assertTrue(kwargs['terminate_on_close'])
            self.assertEqual(kwargs['max_sse_event_size'], 16 * 1024 * 1024)
            events.append('transport-open')
            try:
                yield object(), object()
            finally:
                events.append('transport-close')
        sdk = types.ModuleType('mcp')
        sdk.ClientSession = FakeSession
        http = types.ModuleType('mcp.client.streamable_http')
        http.streamable_http_client = stream
        modules = {'mcp': sdk, 'mcp.client': types.ModuleType('mcp.client'), 'mcp.client.streamable_http': http}
        with patch.dict(sys.modules, modules), patch.dict('os.environ', {'WOA_MCP_TOKEN': 'test-' + 'x' * 48}):
            transport = self.transports.StreamableHTTPTransport('http://127.0.0.1:8000/mcp', enabled=True)
            async with self.clients.MCPClient(transport) as client:
                await client.call_tool('ControlStatus', {})
        self.assertEqual(events, ['transport-open', 'session-open', 'initialize', 'session-close', 'transport-close'])


if __name__ == '__main__':
    unittest.main()
