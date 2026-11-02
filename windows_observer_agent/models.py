"""内部数据模型；这些类型不代表真实 Windows-MCP 协议。"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Observation:
    mock: bool
    observed_at: str | None
    snapshot: dict[str, Any]
    control_status: dict[str, Any]
    source: str
    kind: str = 'mock'
    tool_results: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    field_sources: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ControlState:
    owner: str
    success: bool
    source: str
    error: str | None = None
    state: str | None = None
    generation: int | None = None
    wait_seconds: float | None = None
    historical: bool = False

    @property
    def ready(self) -> bool:
        return self.success and self.owner == 'agent' and self.error is None and not self.historical


@dataclass(frozen=True)
class Monitor:
    id: str | None
    width: int | None
    height: int | None
    source: str


@dataclass(frozen=True)
class UIControl:
    id: str | None
    name: str | None
    type: str | None
    properties: dict[str, Any]
    provider: str | None
    source: str
    # 识别证据不能用作点击验证；v0.1 没有执行器，永远未知。
    click_verified: bool | None = None


@dataclass(frozen=True)
class Window:
    id: str | None
    title: str | None
    app: str | None
    controls: tuple[UIControl, ...]
    source: str


@dataclass(frozen=True)
class Scene:
    observed_at: datetime | None
    desktop_id: str | None
    monitors: tuple[Monitor, ...]
    active_window: Window | None
    windows: tuple[Window, ...]
    observation_success: bool
    ui_tree_truncated: bool
    control: ControlState
    screenshot_ref: str | None
    unknowns: tuple[str, ...]
    errors: tuple[str, ...]
    evidence: tuple[str, ...]
    raw: Observation
    unassigned_controls: tuple[UIControl, ...] = ()


@dataclass(frozen=True)
class PlanStep:
    action: str
    preconditions: tuple[str, ...]
    verification: str
    stop_conditions: tuple[str, ...]
    evidence: tuple[str, ...]
    operation: str = 'review'
    elements: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    goal: str
    status: str
    preconditions: tuple[str, ...]
    assumptions: tuple[str, ...]
    steps: tuple[PlanStep, ...]
    stop_conditions: tuple[str, ...]
    executable: bool = False
    planning_status: str = ''
    planner: str = 'rule'
    goal_understanding: str = ''
    uncertainties: tuple[str, ...] = ()
    confirmations: tuple[str, ...] = ()
    fallback_reason: str | None = None
    execution_permission: str = 'not_granted'

    def __post_init__(self):
        if self.executable is not False or self.execution_permission != 'not_granted':
            raise ValueError('本程序的计划不可执行')
        if not self.planning_status:
            state = {'conditional': 'success', 'unsupported': 'needs_confirmation'}.get(self.status, 'failed')
            object.__setattr__(self, 'planning_status', state)
        if not self.goal_understanding:
            object.__setattr__(self, 'goal_understanding', self.goal)
