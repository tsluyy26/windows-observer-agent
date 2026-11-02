"""控制状态只记录并判断可用性，绝不申请或抢占控制权。"""
from typing import Any

from .models import ControlState


def check_control(status: dict[str, Any], source: str) -> ControlState:
    owner = status.get('owner')
    if owner not in ('agent', 'user', 'none', 'ready', 'takeover_pending', 'unavailable'):
        owner = 'unknown'
    success = status.get('success') is True
    error = status.get('error')
    if error is not None and not isinstance(error, str):
        error = 'ControlStatus error 字段无效'
    if not success:
        error = error if isinstance(error, str) else 'ControlStatus 失败或缺失'
    raw_state = status.get('raw_state')
    raw_state = raw_state if isinstance(raw_state, dict) else {}
    return ControlState(owner, success, source, error,
                        raw_state.get('state'), raw_state.get('generation'),
                        raw_state.get('wait_seconds'), status.get('historical') is True)
