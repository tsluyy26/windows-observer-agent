"""Mock/Replay/显式 Live 观察 CLI，不执行桌面操作。"""
from argparse import SUPPRESS
from dataclasses import asdict, replace
from datetime import datetime
import io
import json
from pathlib import Path
import sys

from .mcp_client import MockClient
from .observer import observe
from .planner import make_plan
from .public_errors import InvalidArguments, SafeArgumentParser, emit_error, error_code, quiet_external_logs
from .reporter import render_report
from .scene_model import build_scene


def _parse_args(argv):
    parser = SafeArgumentParser(description='Windows 桌面观察：Mock、Replay 或显式 Live，只规划不执行')
    parser.add_argument('--goal', default='新建一份空白记事本文档。')
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument('--mock-file', type=Path, help='明确带有 mock:true 的本地 JSON')
    sources.add_argument('--replay-file', type=Path, help='脱敏 MCP 历史回放（无网络）')
    sources.add_argument('--live', action='store_true', help='显式启用认证的本地 Live 观察')
    parser.add_argument('--endpoint', help='Live 必填的 127.0.0.1 MCP HTTP 端点')
    parser.add_argument('--json', action='store_true', help='输出 Scene/Plan JSON，Live 和 Replay 使用脱敏观察数据')
    parser.add_argument('--planner', choices=('rule', 'mock-llm', 'openai', 'provider'), default='rule', help='默认离线规则；provider 使用命名模型配置')
    parser.add_argument('--model', help='外部模型必填标识；无默认付费模型')
    parser.add_argument('--confirm-data-sharing', action='store_true', help='明确确认向所选模型发送脱敏目标和结构化界面信息')
    parser.add_argument('--model-timeout', type=float, help='旧 OpenAI 模式超时，默认 30 秒，最大 120')
    parser.add_argument('--provider-config', type=Path, help='本地多 Provider TOML（不包含密钥）')
    parser.add_argument('--provider', help='显式选择配置中的 Provider，不自动切换')
    parser.add_argument('--validate-provider-config', action='store_true', help='仅本地验证配置，不读取密钥或联网')
    parser.add_argument('--enable-external-model', action='store_true', help='启用所选外部 Provider 请求')
    parser.add_argument('--confirm-provider-endpoint', help='明确确认所选自定义服务的完整 base_url')
    parser.add_argument('--allow-local-http', action='store_true', help='明确允许所选 localhost/127.0.0.1 HTTP 模型服务')
    parser.add_argument('--manual-summary', action='store_true', help=SUPPRESS)
    args = parser.parse_args(argv)
    if bool(args.endpoint) != bool(args.live) or (args.manual_summary and (not args.live or args.json)):
        raise InvalidArguments('live_arguments')
    if (
            (args.planner not in ('openai', 'provider') and (args.model or args.confirm_data_sharing)) or
            (args.planner != 'provider' and not args.validate_provider_config and (
                args.provider_config or args.provider or args.enable_external_model or
                args.confirm_provider_endpoint or args.allow_local_http))):
        raise InvalidArguments()
    return args


def _prepare_provider(args):
    from .providers import ProviderAccess, ProviderManager, ModelPolicyError, load_provider_config

    if args.planner == 'provider' or args.validate_provider_config:
        if not args.provider_config or args.model or args.model_timeout is not None:
            raise ModelPolicyError()
        configs, selected = load_provider_config(args.provider_config.read_text(encoding='utf-8'))
        # 所有命名 Adapter 均须已注册；没有配置动态 import。
        for name in configs:
            ProviderManager(configs, name)
        provider = ProviderManager(configs, args.provider or selected, access=ProviderAccess(
            enabled=args.enable_external_model, share_confirmed=args.confirm_data_sharing,
            confirmed_endpoint=args.confirm_provider_endpoint, allow_local_http=args.allow_local_http))
        if not args.validate_provider_config:
            provider.validate()
        return provider

    if args.planner != 'rule':
        from .llm_provider import MockLLMProvider, OpenAIProvider

        if args.planner == 'mock-llm':
            return MockLLMProvider()
        provider = OpenAIProvider(args.model, enabled=True, share_confirmed=args.confirm_data_sharing,
                                  timeout=args.model_timeout if args.model_timeout is not None else 30.0)
        provider.validate()  # 未获授权时不开始桌面观察。
        return provider
    return None


def _read_local_observation(args):
    if args.replay_file:
        from .replay import ReplayClient

        return observe(ReplayClient(args.replay_file))
    raw = observe(MockClient(args.mock_file))
    # Mock 输入里的 error 可由文件作者填写。公开报告只保留错误发生的位置。
    return replace(raw, source='Mock:local', snapshot=_project_mock_error(raw.snapshot, 'Mock Snapshot 输入报告失败'),
                   control_status=_project_mock_error(raw.control_status, 'Mock 控制状态输入报告失败'))


def _project_mock_error(data, label):
    return dict(data, error=label) if data.get('error') is not None else data


def _encode_report_value(value):
    if type(value) is datetime:
        return value.isoformat(sep=' ')
    raise TypeError('Unsupported report field')


def _manual_summary(scene):
    owner = scene.control.owner
    if owner not in ('agent', 'user', 'none', 'ready', 'takeover_pending', 'unavailable'):
        owner = 'unknown'
    complete = scene.raw.metadata.get('observation_complete') is True
    state = 'complete' if complete else 'incomplete'
    print(f'WOA_REPORT {state} {owner}')
    if not complete:
        emit_error('live', scene.raw.metadata.get('error_code') or 'mcp_snapshot', machine=True)


def main(argv: list[str] | None = None) -> int:
    stage = 'arguments'
    machine = False
    try:
        # Windows 管道与内存测试流保持一致的编码边界。
        for stream in (sys.stdout, sys.stderr):
            if isinstance(stream, io.TextIOWrapper):
                stream.reconfigure(encoding='utf-8', errors='backslashreplace')
        args = _parse_args(argv)
        machine = args.manual_summary
        with quiet_external_logs():
            stage = 'provider'
            provider = _prepare_provider(args)
            if args.validate_provider_config:
                print('Provider 配置有效（仅本地结构/能力/地址验证，未检查密钥或连接）')
                return 0

            stage = 'live' if args.live else 'replay' if args.replay_file else 'mock'
            if args.live:
                from .live import collect_live, public_live_observation

                raw = collect_live(args.endpoint)
            else:
                raw = _read_local_observation(args)
            scene = build_scene(raw)

            stage = 'planning'
            if provider is not None:
                from .llm_planner import plan_with_provider
                from .planning_input import build_prompt, public_planning_scene

                plan = plan_with_provider(args.goal, scene, provider)
                scene = public_planning_scene(scene, build_prompt(args.goal, scene))
            else:
                plan = make_plan(args.goal, scene, review_only=True)
                if args.live:
                    scene = build_scene(public_live_observation(raw))

            stage = 'report'
            if machine:
                _manual_summary(scene)
            elif args.json:
                print(json.dumps({'scene': asdict(scene), 'plan': asdict(plan)},
                                 ensure_ascii=False, indent=2, default=_encode_report_value))
            else:
                print(render_report(scene, plan))
    except KeyboardInterrupt:
        emit_error(stage, 'cancelled', machine=machine)
        return 130
    except Exception as exc:
        emit_error(stage, error_code(exc, stage), machine=machine)
        return 2
    if args.live:
        return 0 if scene.raw.metadata.get('observation_complete') else 1
    return 0 if plan.status == 'conditional' else 1


if __name__ == '__main__':
    raise SystemExit(main())
