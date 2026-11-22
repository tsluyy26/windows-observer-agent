"""执行脚本的纯替身测试与 AST 检查，不进入真实启动路径。"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


class PowerShellTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('pwsh'), '需要 PowerShell 7 进行脚本输出验证')
    def test_agent_report_visible_on_failure_without_leaking_token(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File',
                                 str(root / 'tests' / 'Test-AgentOutput.ps1'),
                                 '-PythonExecutable', sys.executable],
                                cwd=root, capture_output=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('AGENT_OUTPUT_PASS', result.stdout)

    @unittest.skipUnless(shutil.which('pwsh'), '需要 PowerShell 7 验证参数')
    def test_agent_output_requires_explicit_python(self):
        self.check_invalid_python([], 'PythonExecutable is required')

    @unittest.skipUnless(shutil.which('pwsh'), '需要 PowerShell 7 验证参数')
    def test_agent_output_rejects_empty_python(self):
        self.check_invalid_python(['-PythonExecutable', ''], 'PythonExecutable is required')

    @unittest.skipUnless(shutil.which('pwsh'), '需要 PowerShell 7 验证参数')
    def test_agent_output_rejects_invalid_python(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root / 'missing interpreter.exe', root):
            with self.subTest(path=str(path)):
                self.check_invalid_python(['-PythonExecutable', str(path)],
                                          'PythonExecutable must point to an existing file')

    def check_invalid_python(self, arguments, message):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File',
                                 str(root / 'tests' / 'Test-AgentOutput.ps1'), *arguments],
                                cwd=root, capture_output=True, encoding='utf-8', timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)
        self.assertNotIn('AGENT_OUTPUT_PASS', result.stdout)

    @unittest.skipUnless(shutil.which('pwsh'), '需要 PowerShell 7 进行脚本替身验证')
    def test_multiple_server_environments_report_explicit_choices(self):
        self.check_locator('multiple')

    @unittest.skipUnless(shutil.which('pwsh'), '需要 PowerShell 7 进行脚本替身验证')
    def test_missing_server_environment_reports_not_found(self):
        self.check_locator('missing')

    def check_locator(self, scenario):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File',
                                 str(root / 'tests' / 'Test-ServerLocator.ps1'), '-Scenario', scenario],
                                cwd=root, capture_output=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('SERVER_LOCATOR_PASS:' + scenario, result.stdout)

    @unittest.skipUnless(shutil.which('pwsh'), '需要 PowerShell 7 进行脚本替身验证')
    def test_safe_preflight_and_owned_cleanup(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File',
                                 str(root / 'tests' / 'Test-LiveScript.ps1')],
                                cwd=root, capture_output=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('POWERSHELL_SAFETY_TESTS_PASS', result.stdout)


    @unittest.skipUnless(shutil.which('pwsh'), '需要 PowerShell 7 进行认证流程替身验证')
    def test_s5_listener_identity_and_auth_gate(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File',
                                 str(root / 'tests' / 'Test-LiveAuth.ps1')],
                                cwd=root, capture_output=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('LIVE_AUTH_SCRIPT_TESTS_PASS', result.stdout)
        self.assertTrue('a' * 64 not in result.stdout + result.stderr, 'Probe output exposed its credential')

    @unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), '需要 Windows 和 PowerShell 7')
    def test_s8_normal_exit(self):
        self.check_live_job('normal')

    @unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), '需要 Windows 和 PowerShell 7')
    def test_s8_orphans_and_unrelated_process(self):
        self.check_live_job('orphan')

    @unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), '需要 Windows 和 PowerShell 7')
    def test_s8_child_exits_first(self):
        self.check_live_job('exited-child')

    @unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), '需要 Windows 和 PowerShell 7')
    def test_s8_abrupt_fixture_host_exit(self):
        self.check_live_job('host-kill')

    @unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), '需要 Windows 和 PowerShell 7')
    def test_s8_failed_creation_leaves_no_process(self):
        self.check_live_job('start-failure')

    @unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), '需要 Windows 和 PowerShell 7')
    def test_s8_command_line_round_trip(self):
        self.check_live_job('command-line')

    @unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), '需要 Windows 和 PowerShell 7')
    def test_s8_loopback_listener_is_released(self):
        self.check_live_job('port-release')

    def check_live_job(self, scenario):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ['pwsh', '-NoProfile', '-NonInteractive', '-File',
             str(root / 'tests' / 'Test-LiveJob.ps1'), '-Scenario', scenario,
             '-PythonExecutable', sys.executable],
            cwd=root, capture_output=True, encoding='utf-8', timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('LIVE_JOB_TEST_PASS:' + scenario, result.stdout)

    @unittest.skipUnless(shutil.which('pwsh'), '需要 PowerShell 7 验证进程身份')
    def test_s8_listener_membership_and_access_failure(self):
        self.check_live_cleanup('identity')

    @unittest.skipUnless(shutil.which('pwsh'), '需要 PowerShell 7 验证清理诊断')
    def test_s8_cleanup_reports_port_timeout_and_errors(self):
        self.check_live_cleanup('cleanup')

    @unittest.skipUnless(shutil.which('pwsh'), '需要 PowerShell 7 验证启动和取消')
    def test_s8_launch_failure_and_interruption(self):
        self.check_live_cleanup('startup')

    def check_live_cleanup(self, scenario):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ['pwsh', '-NoProfile', '-NonInteractive', '-File',
             str(root / 'tests' / 'Test-LiveCleanup.ps1'), '-Scenario', scenario],
            cwd=root, capture_output=True, encoding='utf-8', timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('LIVE_CLEANUP_TEST_PASS:' + scenario, result.stdout)

    @unittest.skipUnless(shutil.which('pwsh'), '需要 PowerShell 7 验证公开错误')
    def test_s9_manual_live_errors_discard_untrusted_messages(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ['pwsh', '-NoProfile', '-NonInteractive', '-File', str(root / 'tests' / 'Test-PublicLiveErrors.ps1')],
            cwd=root, capture_output=True, encoding='utf-8', timeout=20,
        )
        self.assertFalse('S9_SYNTHETIC_' in result.stdout + result.stderr, 'Script exposed a fixture marker')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('S9_POWERSHELL_ERRORS_PASS', result.stdout)

    @unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), '需要 Windows 和 PowerShell 7')
    def test_s7_venv_and_descendants_receive_only_service_environment(self):
        self.check_live_environment('venv')

    @unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), '需要 Windows 和 PowerShell 7')
    def test_s7_base_python_receives_only_service_environment(self):
        self.check_live_environment('base')

    @unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), '需要 Windows 和 PowerShell 7')
    def test_s7_missing_system_folder_refuses_launch(self):
        self.check_live_environment('missing-folder')

    @unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), '需要 Windows 和 PowerShell 7')
    def test_s7_native_start_failure_never_retries_with_parent_environment(self):
        self.check_live_environment('native-failure')

    def check_live_environment(self, scenario):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ['pwsh', '-NoProfile', '-NonInteractive', '-File',
             str(root / 'tests' / 'Test-LiveEnvironment.ps1'), '-PythonExecutable', sys.executable,
             '-Scenario', scenario],
            cwd=root, capture_output=True, encoding='utf-8', timeout=35,
        )
        self.assertFalse('S7_SYNTHETIC_' in result.stdout + result.stderr or
                         'S7_PROVIDER_' in result.stdout + result.stderr, 'Script exposed a fixture marker')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('S7_LIVE_ENVIRONMENT_PASS:' + scenario, result.stdout)


if __name__ == '__main__':
    unittest.main()
