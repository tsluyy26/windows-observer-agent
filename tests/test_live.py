"""Live 流程测试使用内存传输/假 SDK，不启动或连接桌面服务。"""
import asyncio
import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

from test_mcp import SNAPSHOT, envelope, mocked_replay_file
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
        self.assertEqual(transport.events, ['initialize', 'list_tools', 'ControlStatus', 'Screenshot',
                                            'ControlStatus', 'Snapshot', 'ControlStatus', 'close'])
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


def public_type_sample(*types):
    from windows_observer_agent.mcp_adapter import adapt_records
    from test_mcp import records

    data = records()
    header = SNAPSHOT.split('    ├── (10,20)', 1)[0]
    rows = [f'    ├── ({10 + index},20) {kind} "synthetic-control"'
            for index, kind in enumerate(types)]
    data[1]['result'] = envelope(header + '\n'.join(rows))
    return adapt_records(data, source='MCP:test', kind='mcp')


class LiveTypeRedactionTests(unittest.TestCase):
    def public_controls(self, *types):
        from windows_observer_agent.live import public_live_observation
        from windows_observer_agent.scene_model import build_scene

        public = public_live_observation(public_type_sample(*types))
        return build_scene(public).active_window.controls

    def test_s4_english_uia_types_keep_supported_controls_and_properties(self):
        controls = self.public_controls('button', 'edit', 'Split Button', 'Scroll Bar', 'Hyperlink')
        self.assertEqual([control.type for control in controls],
                         ['button', 'edit', 'split button', 'scroll bar', 'hyperlink'])
        self.assertEqual(controls[0].properties, {
            'coordinates': [10, 20], 'coordinate_space': 'virtual-desktop', 'metadata_text': '',
        })
        self.assertEqual(controls[0].name, 'redacted-control')
        self.assertEqual(controls[0].provider, 'Windows-MCP.semantic-tree')
        self.assertIsNone(controls[0].click_verified)

    def test_s4_confirmed_chinese_types_preserve_localized_labels(self):
        types = ('按钮', '编辑', '编辑框', '菜单项', '选项卡项目', '拆分按钮', '滚动条', '复选框')
        self.assertEqual([control.type for control in self.public_controls(*types)], list(types))

    def test_s4_normalizes_case_and_whitespace_without_partial_matching(self):
        controls = self.public_controls('  BuTToN   ', ' TAB   Item ', 'MENUITEM',
                                        'but ton', 'button unknown', 'Ｂｕｔｔｏｎ')
        self.assertEqual([control.type for control in controls],
                         ['button', 'tab item', 'menuitem', 'unknown', 'unknown', 'unknown'])

    def test_s4_unknown_and_missing_types_are_safe_and_malformed_rows_are_dropped(self):
        from dataclasses import replace
        from windows_observer_agent.live import public_live_observation
        from windows_observer_agent.scene_model import build_scene

        raw = public_type_sample('unrecognized', '', '   ', None, 17, ['invalid'])
        row = ('\n    ├── (19,20) BROKEN_S4_ROW "'
               '\n    ├── (20,20) button"missing separator"')
        snapshot = raw.tool_results[-1]
        snapshot['result']['content'][0]['text'] += row
        scene = build_scene(public_live_observation(raw))
        self.assertEqual([control.type for control in scene.active_window.controls], ['unknown'] * 6)
        self.assertTrue('BROKEN_S4_ROW' not in repr(scene.raw), 'Malformed row reached public output')
        records_with_bad_content = list(raw.tool_results)
        records_with_bad_content[-1] = {**snapshot, 'result': envelope(None)}
        bad_raw = replace(raw, tool_results=tuple(records_with_bad_content))
        failed = build_scene(public_live_observation(bad_raw))
        self.assertFalse(failed.observation_success)

    def test_s4_final_text_and_json_filter_hostile_types_and_retained_records(self):
        from dataclasses import replace
        from windows_observer_agent.__main__ import main

        sensitive = ('S4 Private Window Title', 'S4 Free Text', r'C:\S4Private\synthetic.txt',
                     's4_test_only_credential_9384', 'Bearer sk-s4-fake-credential')
        raw = public_type_sample('Button', *sensitive)
        raw = replace(raw, metadata={'observation_complete': True})
        for options in ([], ['--json']):
            with self.subTest(options=options):
                with patch('windows_observer_agent.live.collect_live', return_value=raw), \
                     contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                    status = main(['--live', '--endpoint', 'http://127.0.0.1:8000/mcp', *options])
                self.assertEqual(status, 0)
                self.assertTrue(not any(marker in out.getvalue() + err.getvalue() for marker in sensitive),
                                'A synthetic marker reached a public Live output')
                if options:
                    scene = json.loads(out.getvalue())['scene']
                    self.assertEqual([c['type'] for c in scene['active_window']['controls']],
                                     ['button'] + ['unknown'] * len(sensitive))
                    self.assertTrue(scene['raw']['metadata']['output_redacted'])
                    snapshot_text = scene['raw']['tool_results'][-1]['result']['content'][0]['text']
                    self.assertIn('unknown "redacted-control"', snapshot_text)
                else:
                    self.assertEqual(out.getvalue().count('redacted-control (unknown)'), len(sensitive))

    def test_s4_replay_and_model_projection_keep_existing_type_policy(self):
        from pathlib import Path
        from windows_observer_agent.planning_input import build_prompt
        from windows_observer_agent.replay import ReplayClient
        from windows_observer_agent.scene_model import build_scene

        raw = public_type_sample('EDIT', 'Split Button', '选项卡项目')
        scene = build_scene(raw)
        prompt = build_prompt('打开窗口', scene)
        self.assertEqual([x['type'] for x in prompt['data']['elements'].values() if x['kind'] == 'control'],
                         ['edit', 'unknown', '选项卡项目'])

        records = [dict(record, redacted=True) for record in raw.tool_results]
        content = json.dumps({'schema': 'woa.replay.v1', 'redacted': True, 'records': records})
        with mocked_replay_file(content):
            replay = build_scene(ReplayClient(Path('synthetic.json')).collect())
        self.assertEqual([control.type for control in replay.active_window.controls],
                         ['EDIT', 'unknown', '选项卡项目'])


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
