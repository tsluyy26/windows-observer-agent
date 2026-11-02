"""通用模型契约：所有服务与凭据均为合成，禁止真实网络。"""
from dataclasses import asdict, replace
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_llm import scene
from windows_observer_agent.planning_input import build_prompt


class ProviderTests(unittest.TestCase):
    def setUp(self):
        from windows_observer_agent.providers import (ProviderConfig, ModelCapabilities, LLMRequest,
            LLMResponse, ProviderRegistry, ProviderManager, ProviderAccess, load_provider_config)
        self.Config, self.Caps, self.Request, self.Response = ProviderConfig, ModelCapabilities, LLMRequest, LLMResponse
        self.Registry, self.Manager, self.Access = ProviderRegistry, ProviderManager, ProviderAccess
        self.load = load_provider_config
        prompt = build_prompt('新建一份空白记事本文档', scene())
        from windows_observer_agent.llm_planner import PLAN_SCHEMA
        self.request = self.Request(**prompt, schema=PLAN_SCHEMA)

    def config(self, **kwargs):
        values = dict(name='example', adapter='openai_chat_completions', model='example-model',
            base_url='https://api.example.com/v1', api_key_env='EXAMPLE_MODEL_KEY',
            capabilities=self.Caps(chat_completions_api=True, json_mode=True))
        values.update(kwargs)
        return self.Config(**values)

    def test_capabilities_do_not_infer_from_provider_name(self):
        caps = asdict(self.Caps())
        self.assertTrue(caps.pop('text_input'))
        self.assertTrue(all(value is False for value in caps.values()))

    def test_mock_unified_response_and_plan(self):
        from windows_observer_agent.llm_planner import plan_with_provider
        manager = self.Manager({'offline': self.Config('offline', 'mock', 'mock-model')}, 'offline')
        result = manager.complete(self.request)
        self.assertIsInstance(result, self.Response)
        plan = plan_with_provider('新建一份空白记事本文档', scene(), manager)
        self.assertEqual(plan.planning_status, 'success')
        self.assertFalse(plan.executable)
        self.assertEqual(plan.execution_permission, 'not_granted')

    def test_custom_registration_accepts_only_unified_response(self):
        from windows_observer_agent.llm_provider import MockLLMProvider, ModelFailure
        registry = self.Registry()
        response = MockLLMProvider().generate(self.request.as_prompt())
        owner = self
        class Custom:
            def complete(self, request):
                return owner.Response(response, 'local_custom', 'custom-model')
        registry.register('custom', lambda config, access: Custom(), external=False)
        config = self.Config('local_custom', 'custom', 'custom-model')
        manager = self.Manager({'local_custom': config}, 'local_custom', registry=registry)
        self.assertEqual(manager.complete(self.request).text, response)
        with self.assertRaises(ValueError):
            registry.register('custom', lambda c, a: Custom())
        with self.assertRaises(ValueError):
            registry.register('package.module:import', lambda c, a: Custom())

    def test_custom_raw_or_mismatched_response_is_rejected(self):
        from windows_observer_agent.llm_provider import ModelFailure
        for result in ({'text': 'raw'}, self.Response('{}', 'wrong', 'other')):
            registry = self.Registry()
            class Custom:
                def complete(self, request):
                    return result
            registry.register('custom', lambda c, a: Custom(), external=False)
            manager = self.Manager({'test': self.Config('test', 'custom', 'm')}, 'test', registry=registry)
            with self.assertRaises(ModelFailure):
                manager.complete(self.request)

    def test_provider_and_model_switch_is_explicit(self):
        configs = {name: self.Config(name, 'mock', model) for name, model in [('first', 'model-one'), ('second', 'model-two')]}
        manager = self.Manager(configs, 'first')
        self.assertEqual(manager.complete(self.request).model, 'model-one')
        manager.select('second')
        self.assertEqual(manager.complete(self.request).model, 'model-two')
        with self.assertRaises(ValueError):
            manager.select('first', model='new-model')
        manager.select('first', model='new-model', capabilities=self.Caps())
        self.assertEqual(manager.complete(self.request).model, 'new-model')
        with self.assertRaises(ValueError):
            manager.select('missing')
        self.assertEqual(manager.complete(self.request).model, 'new-model')

    def test_config_validation_and_selection_are_offline(self):
        with patch('asyncio.run', side_effect=AssertionError('event loop')), patch('socket.socket.connect', side_effect=AssertionError('network')):
            config = self.config()
            config.validate()
            manager = self.Manager({'example': config}, 'example')
            manager.select('example')
        with self.assertRaises(ValueError):
            manager.validate()

    def test_unauthorized_external_never_constructs_custom_adapter(self):
        registry = self.Registry()
        created = []
        registry.register('custom', lambda c, a: created.append(c))
        config = self.config(adapter='custom')
        for access in (self.Access(), self.Access(enabled=True), self.Access(enabled=True, share_confirmed=True)):
            manager = self.Manager({'example': config}, 'example', registry=registry, access=access)
            with patch('asyncio.run', side_effect=AssertionError('loop')), self.assertRaises(ValueError):
                manager.complete(self.request)
        self.assertEqual(created, [])

    def test_endpoint_validation(self):
        bad = ['http://api.example.com/v1', 'http://0.0.0.0/v1', 'http://127.1/v1',
               'http://2130706433/v1', 'http://localhost.evil/v1', 'https://u:p@example.com/v1',
               'https://api.example.com/v1?key=secret', 'https://api.example.com/v1#x',
               'ftp://api.example.com/v1', 'https://api.example.com:99999/v1',
               'https://api.example.com\\@evil/v1']
        for url in bad:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.config(base_url=url).validate()
        for url in ('https://api.example.com/v1', 'http://localhost:8000/v1', 'http://127.0.0.1:8000/v1'):
            self.config(base_url=url).validate()

    def test_http_loopback_and_custom_https_require_exact_runtime_confirmation(self):
        with patch.dict(os.environ, {'EXAMPLE_MODEL_KEY': 'synthetic-only-key'}):
            for url in ('http://localhost:8000/v1', 'http://127.0.0.1:8000/v1'):
                config = self.config(base_url=url)
                access = self.Access(enabled=True, share_confirmed=True, confirmed_endpoint=url)
                manager = self.Manager({'example': config}, 'example', access=access)
                with self.assertRaises(ValueError):
                    manager.validate()
                manager = self.Manager({'example': config}, 'example', access=replace(access, allow_local_http=True))
                manager.validate()
            config = self.config()
            for url in (None, 'https://wrong.example/v1', config.base_url):
                manager = self.Manager({'example': config}, 'example', access=self.Access(enabled=True, share_confirmed=True, confirmed_endpoint=url))
                if url == config.base_url:
                    manager.validate()
                else:
                    with self.assertRaises(ValueError):
                        manager.validate()

    def test_missing_key_and_secret_repr(self):
        config = self.config(extra_params={'temperature': 0.2})
        with patch.dict(os.environ, {}, clear=True):
            manager = self.Manager({'example': config}, 'example', access=self.Access(enabled=True, share_confirmed=True, confirmed_endpoint=config.base_url))
            with self.assertRaises(ValueError):
                manager.validate()
        self.assertNotIn('temperature', repr(config))
        self.assertNotIn(self.request.instructions, repr(self.request))
        self.assertNotIn('SECRET', repr(self.Response('SECRET', 'example', 'm')))

    def test_extra_params_cannot_override_security_or_request_identity(self):
        for params in ({'api_key': 'SECRET'}, {'tools': []}, {'stream': True}, {'model': 'other'},
                       {'extra_headers': {'Authorization': 'SECRET'}}, {'verify': False},
                       {'vendor_options': {'token': 'SECRET'}}, {'temperature': float('nan')},
                       {'access_programs': ['remote']}, {'prompt': {'id': 'hosted-prompt'}}):
            with self.subTest(params=list(params)), self.assertRaises(ValueError):
                self.config(extra_params=params).validate()
        self.config(extra_params={'temperature': 0.1, 'top_p': 0.9, 'vendor_options': {'mode': 'brief'}}).validate()

    def test_tools_images_and_streaming_remain_disabled_even_if_supported(self):
        config = self.Config('offline', 'mock', 'm', capabilities=self.Caps(image_input=True, tool_calling=True, streaming=True))
        manager = self.Manager({'offline': config}, 'offline')
        for request in (replace(self.request, images=('image',)), replace(self.request, tool_calling=True), replace(self.request, streaming=True)):
            with self.assertRaises(ValueError):
                manager.complete(request)

    def test_text_and_api_capability_mismatches_rejected(self):
        for config in (self.config(capabilities=self.Caps()), self.config(capabilities=self.Caps(text_input=False, chat_completions_api=True))):
            with self.assertRaises(ValueError):
                config.validate()
        with self.assertRaises(ValueError):
            self.config(base_url='').validate()

    def test_custom_external_logs_and_secret_response_are_blocked(self):
        import logging
        from windows_observer_agent.providers import ModelFailure
        registry = self.Registry()
        owner = self
        class Custom:
            def complete(self, request):
                logging.warning('synthetic-only-key')
                return owner.Response('synthetic-only-key', 'example', 'example-model')
        registry.register('custom', lambda c, a: Custom())
        config = self.config(adapter='custom')
        manager = self.Manager({'example': config}, 'example', registry=registry,
            access=self.Access(enabled=True, share_confirmed=True, confirmed_endpoint=config.base_url))
        previous = logging.root.manager.disable
        with patch.dict(os.environ, {'EXAMPLE_MODEL_KEY': 'synthetic-only-key'}), self.assertNoLogs(), self.assertRaises(ModelFailure):
            manager.complete(self.request)
        self.assertEqual(logging.root.manager.disable, previous)

    def test_multiple_provider_toml_no_dynamic_import_or_inline_keys(self):
        toml = '''[llm]
provider = "offline"
adapter = "mock"
[llm.providers.offline]
model = "mock-model"
[llm.providers.remote]
adapter = "openai_chat_completions"
base_url = "https://api.example.com/v1"
model = "example-model"
api_key_env = "EXAMPLE_MODEL_KEY"
[llm.providers.remote.capabilities]
text_input = true
chat_completions_api = true
json_mode = true
'''
        configs, selected = self.load(toml)
        self.assertEqual(selected, 'offline')
        self.assertEqual(len(configs), 2)
        for extra in ('api_key = "SECRET"', 'module = "evil"', 'enabled = true'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.load(toml.replace('[llm.providers.offline]', '[llm.providers.offline]\n' + extra))

    def test_cli_config_validate_without_credentials_or_network(self):
        from windows_observer_agent.__main__ import main
        with patch('socket.socket.connect', side_effect=AssertionError('network')), patch('asyncio.run', side_effect=AssertionError('loop')), patch('windows_observer_agent.live.collect_live') as live:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(['--provider-config', 'examples/providers.toml', '--validate-provider-config']), 0)
            self.assertIn('配置有效', out.getvalue())
            live.assert_not_called()

    def test_cli_select_mock_config(self):
        from windows_observer_agent.__main__ import main
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = main(['--planner', 'provider', '--provider-config', 'examples/providers.toml', '--provider', 'offline', '--json'])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(out.getvalue())['plan']['executable'])

    def test_cli_external_config_rejected_before_live_without_all_grants(self):
        from windows_observer_agent.__main__ import main
        base = ['--planner', 'provider', '--provider-config', 'examples/providers.toml',
                '--provider', 'compatible_example', '--live', '--endpoint', 'http://127.0.0.1:8765/mcp']
        cases = ([], ['--enable-external-model'], ['--confirm-data-sharing'],
                 ['--enable-external-model', '--confirm-data-sharing'])
        with patch.dict(os.environ, {'EXAMPLE_MODEL_KEY': 'synthetic-only-key'}), \
             patch('socket.socket.connect', side_effect=AssertionError('network')), \
             patch('asyncio.run', side_effect=AssertionError('loop')), \
             patch('windows_observer_agent.live.collect_live') as live:
            for flags in cases:
                with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(main(base + flags), 2)
                self.assertNotIn('synthetic-only-key', err.getvalue())
            live.assert_not_called()

    def test_config_parser_errors_do_not_echo_inline_secrets(self):
        for text in ('[llm]\napi_key = "SECRET"', '[llm]\ninvalid = "SECRET'):
            with self.assertRaises(ValueError) as raised:
                self.load(text)
            self.assertNotIn('SECRET', str(raised.exception))

    def test_custom_failure_rules_fallback_no_provider_switch(self):
        from windows_observer_agent.llm_planner import plan_with_provider
        registry = self.Registry()
        calls = []
        class Failure:
            def complete(self, request):
                calls.append('first')
                raise RuntimeError('SECRET')
        registry.register('custom', lambda c, a: Failure(), external=False)
        configs = {'first': self.Config('first', 'custom', 'm'), 'second': self.Config('second', 'mock', 'other')}
        manager = self.Manager(configs, 'first', registry=registry)
        plan = plan_with_provider('新建一份空白记事本文档', scene(), manager)
        self.assertEqual(plan.planner, 'rule-fallback')
        self.assertEqual(calls, ['first'])
        self.assertEqual(manager.name, 'first')
        self.assertNotIn('SECRET', repr(plan))

    def test_custom_adapter_cannot_mutate_parser_observation_evidence(self):
        from windows_observer_agent.llm_planner import plan_with_provider
        from windows_observer_agent.llm_provider import MockLLMProvider
        owner = self
        class MutatingAdapter:
            def complete(self, request):
                reply = json.loads(MockLLMProvider().generate(request.as_prompt()))
                request.data['elements']['C999'] = {'kind': 'control', 'evidence': 'E999', 'label': '新建'}
                reply['steps'][0].update(action='审查 C999', elements=['C999'], evidence=['E999'])
                return owner.Response(json.dumps(reply), 'custom', 'm')
        registry = self.Registry()
        registry.register('custom', lambda c, a: MutatingAdapter(), external=False)
        manager = self.Manager({'custom': self.Config('custom', 'custom', 'm')}, 'custom', registry=registry)
        plan = plan_with_provider('新建一份空白记事本文档', scene(), manager)
        self.assertEqual(plan.fallback_reason, 'invalid_plan')
