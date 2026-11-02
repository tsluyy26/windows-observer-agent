"""Mock/Replay/显式 Live 观察 CLI，不执行桌面操作。"""
import argparse
from dataclasses import asdict
import io
import json
from pathlib import Path
import sys

from .mcp_client import MockClient
from .observer import observe
from .planner import make_plan
from .reporter import render_report
from .scene_model import build_scene


def main(argv: list[str] | None = None) -> int:
    # Windows 管道默认可能为 GBK；CLI 输出统一 UTF-8，内存测试流无需重配置。
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description='Windows 桌面观察：Mock、Replay 或显式 Live，只规划不执行')
    parser.add_argument('--goal', default='新建一份空白记事本文档。')
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument('--mock-file', type=Path, help='明确带有 mock:true 的本地 JSON')
    sources.add_argument('--replay-file', type=Path, help='脱敏 MCP 历史回放（无网络）')
    sources.add_argument('--live', action='store_true', help='显式启用认证的本地 Live 观察')
    parser.add_argument('--endpoint', help='Live 必填的 127.0.0.1 MCP HTTP 端点')
    parser.add_argument('--json', action='store_true', help='输出 Scene/Plan JSON，Live 输出自动脱敏')
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
    args = parser.parse_args(argv)
    if bool(args.endpoint) != bool(args.live):
        print('--endpoint 必须与 --live 同时使用', file=sys.stderr)
        return 2
    if args.planner not in ('openai', 'provider') and (args.model or args.confirm_data_sharing):
        print('--model 和数据共享确认只用于显式外部模型模式', file=sys.stderr)
        return 2
    if args.planner != 'provider' and not args.validate_provider_config and (
            args.provider_config or args.provider or args.enable_external_model or args.confirm_provider_endpoint or args.allow_local_http):
        print('命名 Provider 参数须用于 --planner provider 或配置验证', file=sys.stderr)
        return 2
    provider = None
    if args.planner == 'provider' or args.validate_provider_config:
        from .providers import ProviderAccess, ProviderManager, ModelPolicyError, load_provider_config
        try:
            if not args.provider_config:
                raise ModelPolicyError('须指定 --provider-config')
            if args.model or args.model_timeout is not None:
                raise ModelPolicyError('命名配置中的模型/超时在 TOML 中指定；更换模型须明确能力声明')
            configs, selected = load_provider_config(args.provider_config.read_text(encoding='utf-8'))
            # 所有命名 Adapter 均须已注册；没有配置动态 import。
            for name in configs:
                ProviderManager(configs, name)
            provider = ProviderManager(configs, args.provider or selected, access=ProviderAccess(
                enabled=args.enable_external_model, share_confirmed=args.confirm_data_sharing,
                confirmed_endpoint=args.confirm_provider_endpoint, allow_local_http=args.allow_local_http))
            if args.validate_provider_config:
                print('Provider 配置有效（仅本地结构/能力/地址验证，未检查密钥或连接）')
                return 0
            provider.validate()
        except (OSError, ValueError):
            print('Provider 配置/授权无效：检查字段、选择、环境密钥及共享/端点/HTTP 授权；未开始观察或请求', file=sys.stderr)
            return 2
    elif args.planner != 'rule':
        from .llm_provider import MockLLMProvider, OpenAIProvider, ModelPolicyError
        try:
            if args.planner == 'openai':
                provider = OpenAIProvider(args.model, enabled=True,
                    share_confirmed=args.confirm_data_sharing, timeout=args.model_timeout if args.model_timeout is not None else 30.0)
                provider.validate()  # 授权缺失时，连真实桌面观察都不开始。
            else:
                provider = MockLLMProvider()
        except ModelPolicyError as exc:
            print('模型配置拒绝：' + str(exc), file=sys.stderr)
            return 2
    try:
        if args.live:
            from .live import collect_live, public_live_observation
            raw = collect_live(args.endpoint)
            scene = build_scene(raw)
        elif args.replay_file:
            from .replay import ReplayClient
            client = ReplayClient(args.replay_file)
        else:
            client = MockClient(args.mock_file)
        if not args.live:
            scene = build_scene(observe(client))
        if provider is not None:
            from .llm_planner import plan_with_provider
            from .planning_input import build_prompt, public_planning_scene
            plan = plan_with_provider(args.goal, scene, provider)
            scene = public_planning_scene(scene, build_prompt(args.goal, scene))
        else:
            plan = make_plan(args.goal, scene, review_only=True)
            if args.live:
                scene = build_scene(public_live_observation(raw))
    except (OSError, ValueError) as exc:
        print(('Live' if args.live else '回放' if args.replay_file else 'Mock') + ' 读取/解析失败：' + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('观察已取消，会话资源已请求关闭。', file=sys.stderr)
        return 130
    if args.json:
        print(json.dumps({'scene': asdict(scene), 'plan': asdict(plan)},
                         ensure_ascii=False, indent=2, default=str))
    else:
        print(render_report(scene, plan))
    if args.live:
        return 0 if scene.raw.metadata.get('observation_complete') else 1
    return 0 if plan.status == 'conditional' else 1


if __name__ == '__main__':
    raise SystemExit(main())
