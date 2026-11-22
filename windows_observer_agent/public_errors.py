"""Render public error categories independently of exception text and traceback."""
import argparse
from contextlib import contextmanager
import logging
import sys

from .mcp_client import (
    MCPAuthenticationError, MCPCallError, MCPConnectionError, MCPCredentialError,
    MCPPolicyError, MCPResourceError, MCPTimeoutError,
)
from .providers import ModelCredentialError, ModelFailure, ModelPolicyError
from .resource_limits import InputFileError, ResourceLimitError

STAGES = {
    'arguments': '命令参数', 'provider': 'Provider 配置/授权', 'mock': 'Mock 输入',
    'replay': 'Replay 回放', 'live': 'Live', 'planning': '规划', 'report': '报告',
}
MESSAGES = {
    'arguments': '参数无效，使用 --help 查看支持的选项和格式。',
    'live_arguments': '--endpoint 必须与 --live 同时使用，手动摘要仅用于 Live 文本模式。',
    'configuration': '配置或授权无效。检查字段、模型选择、数据共享及服务地址授权。',
    'input_file': '无法读取输入文件。请检查文件是否存在、类型和访问权限。',
    'input_format': '输入格式无效。请检查 UTF-8、JSON 和对应数据结构。',
    'model_credentials': '模型凭据无效或未配置。请检查指定的环境变量。',
    'mcp_credentials': '缺少有效 WOA_MCP_TOKEN 环境变量（至少 32 个 ASCII 非空白字符）。',
    'model_authentication': '模型服务拒绝认证。请检查密钥配置和服务权限。',
    'model_failure': '模型请求失败或结果无效。',
    'mcp_policy': 'MCP 配置或策略检查失败。请检查 Live 的本地端点及工具权限。',
    'mcp_authentication': 'MCP 服务拒绝 Bearer 认证。请核对服务端认证配置。',
    'mcp_connection': 'MCP 连接或初始化失败。请检查服务状态和本地端点。',
    'mcp_tool': 'MCP 工具调用或结果处理失败。',
    'mcp_discovery': '三个必需的只读工具未通过发现检查。',
    'mcp_controller': '控制器不允许观察。',
    'mcp_snapshot': 'Snapshot 观察结果不完整或无法解析。',
    'mcp_shutdown': 'MCP 会话关闭失败。',
    'resource_limit': '输入或服务响应超过安全限额，或编码不受支持。',
    'timeout': '操作超时。',
    'internal': '执行过程中出现内部错误。',
    'unknown': '执行过程中出现未知异常。',
    'cancelled': '观察已取消，会话资源已请求关闭。',
}


class InvalidArguments(ValueError):
    def __init__(self, code='arguments'):
        self.code = code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse includes invalid values and unknown arguments in its messages.
        raise InvalidArguments() from None


@contextmanager
def quiet_external_logs():
    previous = logging.root.manager.disable
    logging.disable(max(previous, logging.CRITICAL))
    try:
        yield
    finally:
        logging.disable(previous)


def error_code(exc, stage):
    if isinstance(exc, InvalidArguments):
        return exc.code
    if isinstance(exc, ModelCredentialError):
        return 'model_credentials'
    if isinstance(exc, ModelFailure):
        return {'model_authentication': 'model_authentication', 'model_timeout': 'timeout',
                'model_resource_limit': 'resource_limit'}.get(exc.code, 'model_failure')
    if isinstance(exc, MCPCredentialError):
        return 'mcp_credentials'
    if isinstance(exc, MCPAuthenticationError):
        return 'mcp_authentication'
    if isinstance(exc, MCPConnectionError):
        return 'mcp_connection'
    if isinstance(exc, (ResourceLimitError, MCPResourceError)):
        return 'resource_limit'
    if isinstance(exc, (TimeoutError, MCPTimeoutError)):
        return 'timeout'
    if isinstance(exc, MCPPolicyError):
        return 'mcp_policy'
    if isinstance(exc, MCPCallError):
        return 'mcp_tool'
    if isinstance(exc, ModelPolicyError):
        return 'configuration'
    if isinstance(exc, InputFileError) or (isinstance(exc, OSError) and stage in ('mock', 'replay', 'provider')):
        return 'input_file'
    if isinstance(exc, (ValueError, RecursionError)) and stage in ('mock', 'replay'):
        return 'input_format'
    if isinstance(exc, ValueError) and stage in ('provider', 'arguments', 'live'):
        return 'configuration'
    if isinstance(exc, (OSError, TypeError, KeyError)):
        return 'internal'
    return 'unknown'


def emit_error(stage, code, *, machine=False):
    safe_code = code if code in MESSAGES else 'unknown'
    if machine:
        print(f'WOA_ERROR {stage} {safe_code}', file=sys.stderr)
    else:
        print(f'{STAGES[stage]} [{safe_code}] {MESSAGES[safe_code]}', file=sys.stderr)
