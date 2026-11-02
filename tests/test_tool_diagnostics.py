"""只保留允许的错误码和结构，不转储服务错误原文。"""
import json
import unittest
from types import SimpleNamespace

from test_mcp import FakeTransport
from windows_observer_agent.mcp_client import MCPCallError, MCPClient


class ReplyTransport(FakeTransport):
    def __init__(self, result=None, error=None):
        super().__init__()
        self.result, self.error = result, error

    async def call_tool(self, name, arguments):
        if self.error:
            raise self.error
        return self.result


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def get_failure(self, transport):
        async with MCPClient(transport) as client:
            with self.assertRaises(MCPCallError) as caught:
                await client.call_tool('Screenshot', {})
        self.assertTrue(transport.closed)
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertNotIn('SECRET', repr(caught.exception.diagnostics))
        return caught.exception

    async def test_control_error_code_preserved_without_private_fields(self):
        for code in ('PHYSICAL_INPUT_HELD', 'USER_CONTROL', 'CONTROL_UNAVAILABLE', 'CONTROL_PREEMPTED'):
            with self.subTest(code=code):
                result = {'isError': True, 'content': [{'type': 'text', 'text': json.dumps({
                    'code': code, 'status': {'state': 'user'}, 'password': 'SECRET'})}]}
                error = await self.get_failure(ReplyTransport(result))
                self.assertEqual(error.diagnostics['control_code'], code)
                self.assertEqual(error.diagnostics['category'], 'control_rejected')
                self.assertIn(code, str(error))

    async def test_capture_error_text_has_separate_category(self):
        result = {'content': [{'type': 'text', 'text': 'Error capturing screenshot: SECRET. Please try again.'}]}
        error = await self.get_failure(ReplyTransport(result))
        self.assertEqual(error.diagnostics['category'], 'server_capture_error')

    async def test_sdk_schema_error_is_classified_without_message(self):
        error = await self.get_failure(ReplyTransport(error=RuntimeError(
            'Tool Screenshot has an output schema but did not return structured content SECRET')))
        self.assertEqual(error.diagnostics['category'], 'sdk_output_schema')

    async def test_sdk_protocol_error_preserves_numeric_code_only(self):
        exc = RuntimeError('SECRET')
        exc.error = SimpleNamespace(code=-32000, message='SECRET')
        error = await self.get_failure(ReplyTransport(error=exc))
        self.assertEqual(error.diagnostics['protocol_code'], -32000)

    async def test_unknown_payload_reports_shape_not_contents(self):
        error = await self.get_failure(ReplyTransport({'content': None, 'SECRET': 'SECRET'}))
        self.assertEqual(error.diagnostics['category'], 'result_parse_error')


if __name__ == '__main__':
    unittest.main()
