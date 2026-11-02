"""真实锁定 OpenAI SDK + 内存 HTTP，不使用真实密钥或付费 API。"""
import asyncio
import contextlib
import importlib.util
import io
import json
import logging
import os
import unittest
from unittest.mock import patch

from test_llm import scene
from windows_observer_agent.llm_provider import OpenAIProvider, ModelPolicyError, ModelFailure, MockLLMProvider
from windows_observer_agent.planning_input import build_prompt


class ModelAuthorizationTests(unittest.TestCase):
    def test_missing_consent_key_model_rejected_without_event_loop(self):
        with patch.dict(os.environ, {}, clear=True), patch('asyncio.run', side_effect=AssertionError('event loop')):
            for model, enabled, shared in [('m', False, False), ('m', True, False), ('m', True, True), (None, True, True)]:
                with self.subTest(model=model, enabled=enabled, shared=shared), self.assertRaises(ModelPolicyError):
                    OpenAIProvider(model, enabled=enabled, share_confirmed=shared).generate({})

    def test_bad_timeout_rejected(self):
        for value in (True, 0, -1, float('inf'), float('nan'), 121, '30'):
            with self.subTest(timeout=value), self.assertRaises(ModelPolicyError):
                OpenAIProvider('m', timeout=value)


@unittest.skipUnless(importlib.util.find_spec('openai'), '需安装 llm extra')
class ModelSDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx2
        self.httpx = httpx2
        self.actual_client = httpx2.AsyncClient
        self.clients, self.requests = [], []
        self.status = 200
        self.delay = 0
        self.fail_network = False
        self.incomplete = False
        self.nontext = False
        self.reasoning = False
        self.entered = asyncio.Event()
        self.prompt = build_prompt('新建一份空白记事本文档。', scene())

    async def handler(self, request):
        self.entered.set()
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_network:
            raise self.httpx.ConnectError('SECRET private body', request=request)
        if self.status != 200:
            return self.httpx.Response(self.status, json={'error': {'message': 'SECRET'}}, headers={'Location': 'https://example.com/leak'})
        reply = MockLLMProvider().generate(self.prompt)
        output = [{'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                   'content': [{'type': 'output_text', 'text': reply, 'annotations': []}]}]
        if self.nontext:
            output = [{'id': 'call_test', 'type': 'function_call', 'call_id': 'x', 'name': 'Click', 'arguments': '{}'}]
        if self.reasoning:
            output.insert(0, {'id': 'reason_test', 'type': 'reasoning', 'summary': []})
        return self.httpx.Response(200, json={'id': 'resp_test', 'created_at': 1, 'object': 'response',
            'model': 'synthetic-test-model', 'status': 'incomplete' if self.incomplete else 'completed',
            'error': None, 'incomplete_details': {'reason': 'max_output_tokens'} if self.incomplete else None,
            'output': output})

    @contextlib.contextmanager
    def sandbox(self):
        owner = self
        class MemoryClient(self.actual_client):
            def __init__(self, **kwargs):
                owner.assertFalse(kwargs['trust_env'])
                owner.assertFalse(kwargs['follow_redirects'])
                super().__init__(transport=owner.httpx.MockTransport(owner.handler), **kwargs)
                owner.clients.append(self)
        with patch.dict(os.environ, {'WOA_LLM_API_KEY': 'synthetic-test-secret', 'OPENAI_BASE_URL': 'https://evil.example', 'OPENAI_LOG': 'debug'}), \
             patch('httpx2.AsyncClient', MemoryClient), \
             patch('socket.socket.connect', side_effect=AssertionError('real network')), \
             patch('subprocess.Popen', side_effect=AssertionError('process')):
            yield

    def provider(self, timeout=2):
        return OpenAIProvider('synthetic-test-model', enabled=True, share_confirmed=True, timeout=timeout)

    async def test_real_sdk_request_shape_privacy_and_close(self):
        previous = logging.root.manager.disable
        with self.sandbox():
            text = await self.provider().generate_async(self.prompt)
        self.assertFalse(json.loads(text)['executable'])
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        body = json.loads(request.content)
        self.assertEqual(str(request.url), 'https://api.openai.com/v1/responses')
        self.assertEqual(request.headers['Authorization'], 'Bearer synthetic-test-secret')
        self.assertEqual(body['model'], 'synthetic-test-model')
        self.assertEqual(body['tools'], [])
        self.assertEqual(body['tool_choice'], 'none')
        self.assertFalse(body['store'])
        self.assertFalse(body['stream'])
        self.assertTrue(body['text']['format']['strict'])
        self.assertEqual(body['text']['format']['type'], 'json_schema')
        self.assertNotIn('synthetic-test-secret', request.content.decode())
        self.assertNotIn('项目说明', request.content.decode())
        self.assertTrue(all(c.is_closed for c in self.clients))
        self.assertEqual(logging.root.manager.disable, previous)

    async def test_authentication_and_server_failure_no_retry(self):
        for status in (401, 403, 429, 500):
            self.status = status
            self.requests.clear()
            with self.subTest(status=status), self.sandbox(), self.assertRaises(ModelFailure) as raised:
                await self.provider().generate_async(self.prompt)
            self.assertNotIn('SECRET', str(raised.exception))
            if status in (401, 403):
                self.assertEqual(raised.exception.code, 'model_authentication')
            self.assertEqual(len(self.requests), 1)
            self.assertTrue(all(c.is_closed for c in self.clients))

    async def test_redirect_is_not_followed(self):
        self.status = 307
        with self.sandbox(), self.assertRaises(ModelFailure):
            await self.provider().generate_async(self.prompt)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(all(c.is_closed for c in self.clients))

    async def test_network_error_is_scrubbed(self):
        self.fail_network = True
        with self.assertNoLogs(level='DEBUG'), self.sandbox(), self.assertRaises(ModelFailure) as raised:
            await self.provider().generate_async(self.prompt)
        self.assertNotIn('SECRET', str(raised.exception))
        self.assertTrue(all(c.is_closed for c in self.clients))

    async def test_timeout_cancels_and_closes(self):
        self.delay = 10
        with self.sandbox(), self.assertRaises(ModelFailure) as raised:
            await self.provider(timeout=0.02).generate_async(self.prompt)
        self.assertEqual(raised.exception.code, 'model_timeout')
        self.assertTrue(all(c.is_closed for c in self.clients))

    async def test_external_cancellation_closes_and_restores_logging(self):
        self.delay = 10
        previous = logging.root.manager.disable
        with self.sandbox():
            task = asyncio.create_task(self.provider().generate_async(self.prompt))
            await asyncio.wait_for(self.entered.wait(), timeout=3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(all(c.is_closed for c in self.clients))
        self.assertEqual(logging.root.manager.disable, previous)

    async def test_incomplete_and_tool_output_rejected(self):
        for field in ('incomplete', 'nontext'):
            setattr(self, field, True)
            with self.subTest(field=field), self.sandbox(), self.assertRaises(ModelFailure):
                await self.provider().generate_async(self.prompt)
            setattr(self, field, False)
        self.assertTrue(all(c.is_closed for c in self.clients))

    async def test_reasoning_metadata_is_ignored_but_json_message_is_read(self):
        self.reasoning = True
        with self.sandbox():
            text = await self.provider().generate_async(self.prompt)
        self.assertFalse(json.loads(text)['executable'])
        self.assertTrue(all(c.is_closed for c in self.clients))


if __name__ == '__main__':
    unittest.main()
