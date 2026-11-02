"""Live 流程测试使用内存传输/假 SDK，不启动或连接桌面服务。"""
import asyncio
import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

from test_mcp import SNAPSHOT, envelope
from windows_observer_agent.mcp_client import MCPClient, MCPConnectionError, MCPPolicyError


class LiveTransport:
    def __init__(self, state='ready', *, fail=None, missing=False, tree=SNAPSHOT):
        self.state, self.fail, self.tree, self.missing = state, fail, tree, missing
        self.events = []
        self.server_info = {'name': 'windows-mcp', 'version': 'test'}

    async def connect(self): self.events.append('initialize')
    async def list_tools(self):
        self.events.append('list_tools')
        return {'tools': [{'name': x} for x in ('ControlStatus', 'Screenshot', 'Snapshot', 'Click')
                         if not (self.missing and x == 'Snapshot')]}
    async def call_tool(self, name, arguments):
        self.events.append(name)
        if name == self.fail:
            return {'isError': True, 'content': [{'type': 'text', 'text': 'SECRET failure'}]}
        if name == 'ControlStatus':
            return {'structuredContent': {'state': self.state, 'generation': 1, 'wait_seconds': 0}}
        if name == 'Screenshot':
            return {'content': [{'type': 'text', 'text': 'Screenshot Size: (1920,1080)'},
                                {'type': 'image', 'mimeType': 'image/png', 'data': 'SECRET_IMAGE'}]}
        return envelope(self.tree)
    async def close(self): self.events.append('close')


class LiveFlowTests(unittest.IsolatedAsyncioTestCase):
    async def run_live(self, transport):
        from windows_observer_agent.live import observe_live
        return await observe_live(MCPClient(transport))

    async def test_initialize_discover_control_screenshot_snapshot_in_order(self):
        transport = LiveTransport()
        raw = await self.run_live(transport)
        self.assertEqual(transport.events, ['initialize', 'list_tools', 'ControlStatus', 'Screenshot', 'Snapshot', 'close'])
        self.assertTrue(raw.metadata['observation_complete'])
        self.assertTrue(raw.snapshot['success'])
        self.assertEqual(raw.kind, 'mcp')
        self.assertNotIn('SECRET_IMAGE', repr(raw))
        self.assertTrue(all(r['observed_at'].endswith('+00:00') for r in raw.tool_results))

    async def test_user_pending_unavailable_unknown_stop_before_images(self):
        for state in ('user', 'takeover_pending', 'unavailable', 'unknown'):
            with self.subTest(state=state):
                transport = LiveTransport(state)
                raw = await self.run_live(transport)
                self.assertFalse(raw.metadata['observation_complete'])
                self.assertNotIn('Screenshot', transport.events)
                self.assertNotIn('Snapshot', transport.events)
                self.assertIn('close', transport.events)

    async def test_ai_state_kept_distinct_from_ready(self):
        raw = await self.run_live(LiveTransport('ai'))
        self.assertEqual(raw.control_status['owner'], 'agent')
        self.assertEqual(raw.control_status['raw_state']['state'], 'ai')

    async def test_missing_tools_stops_before_any_tool_call(self):
        transport = LiveTransport(missing=True)
        raw = await self.run_live(transport)
        self.assertFalse(raw.metadata['observation_complete'])
        self.assertEqual(transport.events, ['initialize', 'list_tools', 'close'])

    async def test_tool_failure_stops_without_retry_and_preserves_partial_results(self):
        for name in ('ControlStatus', 'Screenshot', 'Snapshot'):
            with self.subTest(tool=name):
                transport = LiveTransport(fail=name)
                raw = await self.run_live(transport)
                self.assertFalse(raw.metadata['observation_complete'])
                self.assertEqual(transport.events.count(name), 1)
                self.assertNotIn('SECRET', repr(raw))
                if name == 'Screenshot':
                    from windows_observer_agent.live import public_live_observation
                    from windows_observer_agent.scene_model import build_scene
                    public = public_live_observation(raw)
                    scene = build_scene(public)
                    self.assertEqual(public.metadata['attempted_tools'], ['ControlStatus', 'Screenshot'])
                    self.assertIn('Screenshot', ' '.join(scene.errors))

    async def test_truncated_tree_keeps_limit_and_unknowns(self):
        raw = await self.run_live(LiveTransport(tree=SNAPSHOT + '\n... [truncated: reached the 25-element capture limit]'))
        self.assertTrue(raw.snapshot['truncation_reported'])
        self.assertEqual(raw.snapshot['capture_limit'], 25)

    async def test_incomplete_snapshot_does_not_report_success(self):
        raw = await self.run_live(LiveTransport(tree='UI Tree: skipped'))
        self.assertFalse(raw.metadata['observation_complete'])


class LivePolicyTests(unittest.TestCase):
    def test_live_report_includes_stop_reason(self):
        from dataclasses import replace
        from windows_observer_agent.mcp_adapter import adapt_records
        from windows_observer_agent.live import public_live_observation
        from windows_observer_agent.scene_model import build_scene
        from windows_observer_agent.planner import make_plan
        from windows_observer_agent.reporter import render_report
        from test_mcp import records
        raw = replace(adapt_records(records(), source='MCP:test', kind='mcp'),
                      metadata={'observation_complete': False, 'stop_reason': '控制器不允许观察：user'})
        scene = build_scene(public_live_observation(raw))
        report = render_report(scene, make_plan('新建一份空白记事本文档。', scene))
        self.assertIn('停止原因：控制器不允许观察：user', report)

    def test_missing_token_rejected_before_sdk_or_network(self):
        from windows_observer_agent.transports import StreamableHTTPTransport
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(MCPPolicyError):
                StreamableHTTPTransport('http://127.0.0.1:8000/mcp', enabled=True).validate_live()

    def test_illegal_addresses(self):
        from windows_observer_agent.transports import StreamableHTTPTransport
        for url in ('http://localhost:8000/mcp', 'http://[::1]:8000/mcp', 'http://0.0.0.0/mcp',
                    'http://127.0.0.1.evil/mcp', 'http://2130706433/mcp', 'http://127.0.0.1:99999/mcp',
                    'http://u:p@127.0.0.1/mcp', 'http://127.0.0.1/mcp?token=x',
                    'https://example.com/mcp', 'http://127.0.0.1/mcp#x'):
            with self.subTest(url=url), self.assertRaises(MCPPolicyError):
                StreamableHTTPTransport(url)

    def test_endpoint_requires_explicit_live(self):
        from windows_observer_agent.__main__ import main
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(['--endpoint', 'http://127.0.0.1:8000/mcp']), 2)
        self.assertIn('live', err.getvalue().lower())

    def test_live_missing_token_cli_does_not_print_secret_or_traceback(self):
        from windows_observer_agent.__main__ import main
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(['--live', '--endpoint', 'http://127.0.0.1:8000/mcp']), 2)
        self.assertIn('WOA_MCP_TOKEN', err.getvalue())
        self.assertNotIn('Traceback', err.getvalue())

    def test_live_not_enabled_never_constructs_transport(self):
        from windows_observer_agent.__main__ import main
        with patch('windows_observer_agent.transports.StreamableHTTPTransport') as factory:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([]), 0)
            factory.assert_not_called()

    def test_live_cli_success_is_observation_success_even_planner_blocked(self):
        from windows_observer_agent.__main__ import main
        from windows_observer_agent.mcp_adapter import adapt_records
        from test_mcp import records
        from dataclasses import replace
        raw = replace(adapt_records(records(), source='MCP:test', kind='mcp'),
                      metadata={'observation_complete': True})
        with patch('windows_observer_agent.live.collect_live', return_value=raw), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(['--live', '--endpoint', 'http://127.0.0.1:8000/mcp']), 0)
        self.assertNotIn('PrivateDoc', out.getvalue())
        self.assertNotIn('secret-value', out.getvalue())
        self.assertIn('视觉分析', out.getvalue())


if __name__ == '__main__':
    unittest.main()
