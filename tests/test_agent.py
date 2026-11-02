"""行为验收；所有样本均为 Mock，不接触真实桌面。"""
import contextlib
import importlib
import io
import json
import sys
import unittest


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.client_module = importlib.import_module('windows_observer_agent.mcp_client')
        self.scene_module = importlib.import_module('windows_observer_agent.scene_model')
        self.planner = importlib.import_module('windows_observer_agent.planner')
        self.observer = importlib.import_module('windows_observer_agent.observer')
        self.data = self.client_module.MockClient().read_mock()

    def scene(self, data=None):
        return self.scene_module.build_scene(self.observer.observe(
            self.client_module.MockClient(data=self.data if data is None else data)))

    def test_active_window_and_evidence(self):
        scene = self.scene()
        self.assertEqual(scene.active_window.title, '项目说明 - 记事本')
        self.assertEqual(scene.desktop_id, 'mock-desktop-1')
        self.assertEqual(scene.monitors[0].width, 1920)
        self.assertIn('/snapshot/windows/0', scene.active_window.source)
        self.assertTrue(scene.observation_success)
        self.assertIsNone(scene.active_window.controls[0].click_verified)
        self.assertTrue(scene.raw.mock)

    def test_not_observed_does_not_mean_not_installed(self):
        self.data['snapshot']['windows'] = []
        self.data['snapshot']['active_window_id'] = None
        plan = self.planner.make_plan('新建一份空白记事本文档。', self.scene())
        self.assertEqual(plan.status, 'conditional')
        self.assertIn('未观察到', ' '.join(plan.assumptions))
        self.assertNotIn('未安装', ' '.join(step.action for step in plan.steps))
        self.assertIn('查找', plan.steps[0].action)

    def test_500_elements_conservatively_marks_truncation(self):
        controls = self.data['snapshot']['windows'][0]['controls']
        controls[:] = [dict(controls[0], id=str(i)) for i in range(500)]
        self.assertTrue(self.scene().ui_tree_truncated)
        controls.pop()
        self.assertFalse(self.scene().ui_tree_truncated)

    def test_missing_window_information_is_unknown(self):
        self.data['snapshot']['windows'] = [{'id': 'unknown'}, None, {}]
        scene = self.scene()
        self.assertIsNone(scene.active_window)
        self.assertIsNone(scene.windows[0].title)
        self.assertTrue(scene.unknowns)

    def test_user_control_blocks_plan(self):
        self.data['control_status']['owner'] = 'user'
        scene = self.scene()
        self.assertEqual(scene.control.owner, 'user')
        plan = self.planner.make_plan('新建一份空白记事本文档。', scene)
        self.assertEqual(plan.status, 'blocked')
        self.assertEqual(plan.steps, ())
        report = importlib.import_module('windows_observer_agent.reporter').render_report(scene, plan)
        self.assertIn('user', report)
        self.assertIn('停止', report)

    def test_plan_has_conditions_verification_and_stop_rules(self):
        plan = self.planner.make_plan('新建一份空白记事本文档。', self.scene())
        self.assertTrue(plan.preconditions)
        self.assertTrue(plan.stop_conditions)
        self.assertFalse(plan.executable)
        for step in plan.steps:
            self.assertTrue(step.preconditions)
            self.assertTrue(step.verification)
            self.assertTrue(step.stop_conditions)
        self.assertIn('新建', ' '.join(s.action for s in plan.steps))

    def test_offline_cli_no_process_or_network(self):
        # Audit catches lower-level process creation too, not just Popen.
        violations = []
        guard = {'enabled': True}
        def audit(event, args):
            if guard['enabled'] and (event.startswith(('subprocess.', 'socket.'))
                                     or event in {'os.system', 'os.spawn', 'os.startfile',
                                                  'os.posix_spawn', 'os.exec'}):
                violations.append(event)
                raise AssertionError('Offline side effect: ' + event)
        sys.addaudithook(audit)
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                cli = importlib.import_module('windows_observer_agent.__main__')
                self.assertEqual(cli.main([]), 0)
            self.assertIn('Mock', out.getvalue())
            self.assertEqual(violations, [])
        finally:
            guard['enabled'] = False

    def test_failed_observation_blocks_plan(self):
        self.data['snapshot']['success'] = False
        self.data['snapshot']['error'] = 'Mock timeout'
        scene = self.scene()
        self.assertFalse(scene.observation_success)
        self.assertIn('Mock timeout', ' '.join(scene.errors))
        self.assertEqual(self.planner.make_plan('新建一份空白记事本文档。', scene).status, 'blocked')

    def test_unknown_controller_fails_closed(self):
        for status in ({}, {'owner': 'alien'}, {'owner': 'agent', 'success': False},
                       {'owner': 'agent', 'success': 'false'}):
            with self.subTest(status=status):
                self.data['control_status'] = status
                self.assertFalse(self.scene().control.ready)

    def test_mock_marker_required(self):
        self.data['mock'] = False
        with self.assertRaises(ValueError):
            self.scene()

    def test_explicit_truncation_and_null_sections(self):
        self.data['snapshot']['ui_tree_truncated'] = True
        self.assertTrue(self.scene().ui_tree_truncated)
        self.data['snapshot'] = None
        self.assertFalse(self.scene().observation_success)

    def test_inactive_notepad_and_unknown_target(self):
        self.data['snapshot']['active_window_id'] = 'mock-browser'
        plan = self.planner.make_plan('新建一份空白记事本文档。', self.scene())
        self.assertIn('激活', plan.steps[0].action)
        self.assertEqual(self.planner.make_plan('删除所有文件', self.scene()).status, 'unsupported')
        self.assertEqual(self.planner.make_plan('不要新建一份空白记事本文档', self.scene()).status, 'unsupported')

    def test_json_cli_and_read_error(self):
        cli = importlib.import_module('windows_observer_agent.__main__')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(['--json']), 0)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload['scene']['raw']['mock'])
        self.assertFalse(payload['plan']['executable'])
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(['--mock-file', 'no-such-mock.json']), 2)
        self.assertIn('Mock', err.getvalue())

    def test_observation_is_a_copy(self):
        scene = self.scene()
        self.data['snapshot']['windows'].clear()
        self.assertEqual(len(scene.raw.snapshot['windows']), 2)

    def test_bad_metadata_is_reported(self):
        self.data['observed_at'] = 'bad-time'
        self.data['snapshot']['monitors'] = [{'width': -1, 'height': 'x'}]
        scene = self.scene()
        self.assertIsNone(scene.observed_at)
        self.assertIsNone(scene.monitors[0].width)
        self.assertTrue(scene.unknowns)

    def test_redirected_report_is_utf8(self):
        cli = importlib.import_module('windows_observer_agent.__main__')
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding='gbk')
        with contextlib.redirect_stdout(stream):
            self.assertEqual(cli.main([]), 0)
        stream.flush()
        self.assertIn('离线 Mock 报告', buffer.getvalue().decode('utf-8'))

    def test_error_in_control_result_blocks_plan(self):
        self.data['control_status']['error'] = 'Mock inconsistent result'
        self.assertFalse(self.scene().control.ready)


if __name__ == '__main__':
    unittest.main()
