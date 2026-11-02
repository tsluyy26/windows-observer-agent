"""执行脚本的纯替身测试与 AST 检查，不进入真实启动路径。"""
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


if __name__ == '__main__':
    unittest.main()
