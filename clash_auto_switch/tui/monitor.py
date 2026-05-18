import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, Optional

from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from clash_auto_switch.core.clash_state import ClashProxyState
from clash_auto_switch.defs import ProxyServicePair, ServiceRecord
from clash_auto_switch.tui.keyboard import KeyboardInput
from clash_auto_switch.core.storage import NodeHistoryStorage


SwitchNodeFunc = Callable[[ProxyServicePair, str], Awaitable[None]]
ToggleNodeDisabledFunc = Callable[[ProxyServicePair, str], Awaitable[None]]

MIN_SERVICE_ROWS = 4
MIN_EVENT_LINES = 4
MAX_EVENT_LINES = 10


@dataclass
class NodeScore:
    name: str
    score: float = 0.0
    status: str = "unknown"
    total_checks: int = 0
    successful_checks: int = 0
    disabled: bool = False

    @property
    def success_rate(self) -> float:
        if self.total_checks <= 0:
            return 0.0
        return self.successful_checks / self.total_checks


@dataclass
class ServiceView:
    task: ProxyServicePair
    current_node: Optional[str] = None
    last_status: str = "等待检测"
    nodes: list[NodeScore] = field(default_factory=list)
    selected_node_index: int = 0


class MonitorTui:
    """Dynamic terminal UI for monitor mode."""

    def __init__(self, tasks: list[ProxyServicePair], *, max_events: int = 50) -> None:
        self._service_order = [task.service_name for task in tasks]
        self._services = {
            task.service_name: ServiceView(task=task)
            for task in tasks
        }
        self._events: deque[str] = deque(maxlen=max_events)
        self._live: Optional[Live] = None
        self._selected_service_index: Optional[int] = None
        self._console = Console()

    def __enter__(self) -> "MonitorTui":
        self._live = Live(self.render(), refresh_per_second=4, screen=True, console=self._console)
        self._live.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._live is not None:
            self._live.__exit__(exc_type, exc, traceback)
            self._live = None

    def event(self, service_name: str, message: str, *, level: str = "info") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._events.append(f"[{timestamp}] [{service_name}] {message}")
        if service_name in self._services:
            self._services[service_name].last_status = message
        self.refresh()

    async def refresh_service(
        self,
        clash: ClashProxyState,
        task: ProxyServicePair,
        storage: NodeHistoryStorage,
    ) -> None:
        service = self._services.get(task.service_name)
        if service is None:
            return

        try:
            group_state = await clash.get_proxy_group(task.proxy_group_name)
        except Exception as exc:
            self.event(task.service_name, f"读取代理组失败: {exc}", level="warning")
            return

        service.current_node = group_state.now
        service.nodes = build_node_scores(
            group_state.nodes,
            current_node=service.current_node,
            service_name=task.service_name,
            proxy_group_name=task.proxy_group_name,
            storage=storage,
        )
        if service.nodes:
            service.selected_node_index = min(service.selected_node_index, len(service.nodes) - 1)
        else:
            service.selected_node_index = 0
        self.refresh()

    async def run_interaction(
        self,
        switch_node: SwitchNodeFunc,
        toggle_node_disabled: Optional[ToggleNodeDisabledFunc] = None,
    ) -> None:
        with KeyboardInput() as keyboard:
            while True:
                key = keyboard.read_key()
                if key is None:
                    await asyncio.sleep(0.05)
                    continue

                action = self.handle_key(key)
                if action == "quit":
                    self.event("system", "退出")
                    return
                if action == "switch":
                    selection = self.selected_node()
                    if selection is None:
                        self.event("system", "没有可切换的节点")
                        continue
                    task, node_name = selection
                    try:
                        await switch_node(task, node_name)
                    except Exception as exc:
                        self.event(task.service_name, f"手动切换失败 | {exc}")
                if action == "toggle_disabled":
                    if toggle_node_disabled is None:
                        self.event("system", "当前模式不支持禁用节点")
                        continue
                    selection = self.selected_node()
                    if selection is None:
                        self.event("system", "没有可禁用的节点")
                        continue
                    task, node_name = selection
                    try:
                        await toggle_node_disabled(task, node_name)
                    except Exception as exc:
                        self.event(task.service_name, f"禁用节点失败 | {exc}")

                self.refresh()

    def handle_key(self, key: str) -> Optional[str]:
        if key == "q":
            return "quit"
        if key == "h":
            self._move_service(-1)
        elif key == "l":
            self._move_service(1)
        elif key == "j":
            self._move_node(1)
        elif key == "k":
            self._move_node(-1)
        elif key == "enter":
            return "switch"
        elif key == "d":
            return "toggle_disabled"
        return None

    def selected_node(self) -> Optional[tuple[ProxyServicePair, str]]:
        service = self._selected_service()
        if service is None or not service.nodes:
            return None
        node = service.nodes[service.selected_node_index]
        return service.task, node.name

    def _move_service(self, delta: int) -> None:
        if not self._service_order:
            return
        if self._selected_service_index is None:
            self._selected_service_index = 0 if delta >= 0 else len(self._service_order) - 1
            return
        self._selected_service_index = (self._selected_service_index + delta) % len(self._service_order)

    def _move_node(self, delta: int) -> None:
        if self._selected_service_index is None:
            self._selected_service_index = 0
        service = self._selected_service()
        if service is None or not service.nodes:
            return
        service.selected_node_index = (service.selected_node_index + delta) % len(service.nodes)

    def _selected_service(self) -> Optional[ServiceView]:
        if not self._service_order or self._selected_service_index is None:
            return None
        service_name = self._service_order[self._selected_service_index]
        return self._services.get(service_name)

    def refresh(self) -> None:
        if self._live is not None:
            self._live.update(self.render())

    def render(self) -> Group:
        service_rows, event_lines = self._layout_heights()
        panels = [
            Panel(
                self._render_service_table(service, max_rows=service_rows),
                title=f"{service.task.service_name}  |  {service.task.proxy_group_name}",
                border_style="magenta" if self._is_selected_service(service) else "cyan",
                height=service_rows + 4,
            )
            for service in self._services.values()
        ]
        service_columns = Columns(panels, equal=True, expand=True)
        return Group(
            service_columns,
            Panel(
                self._render_event_log(event_lines),
                title="关键事件",
                border_style="yellow",
                height=event_lines + 2,
            ),
            self._render_status_bar(),
        )

    def _layout_heights(self) -> tuple[int, int]:
        terminal_height = max(self._console.size.height, 12)
        event_lines = min(MAX_EVENT_LINES, max(MIN_EVENT_LINES, terminal_height // 5))
        service_rows = terminal_height - event_lines - 8
        return max(MIN_SERVICE_ROWS, service_rows), event_lines

    def _render_service_table(self, service: ServiceView, *, max_rows: int) -> Table:
        table = Table(expand=True, show_lines=False)
        current_node = service.current_node or "-"
        table.caption = f"当前节点: {current_node} | 最近状态: {service.last_status}"
        table.add_column("节点")
        table.add_column("得分", width=8, justify="right")
        table.add_column("成功率", width=8, justify="right")

        if not service.nodes:
            table.add_row("等待读取 ProxyGroup 节点", "-", "-")
            return table

        visible_nodes = _visible_node_window(service.nodes, service.selected_node_index, max_rows)
        for index, node in visible_nodes:
            is_current = node.name == service.current_node
            table.add_row(
                Text(
                    _node_display_name(node, current=is_current),
                    style=_node_name_style(
                        node,
                        selected=self._is_selected_service(service) and index == service.selected_node_index,
                        current=is_current,
                    ),
                ),
                f"{node.score:.3f}",
                f"{node.success_rate:.0%}" if node.total_checks else "-",
            )
        hidden_count = len(service.nodes) - len(visible_nodes)
        if hidden_count > 0:
            table.caption = f"{table.caption} | 隐藏 {hidden_count} 个节点"
        return table

    def _render_event_log(self, max_lines: int) -> Text:
        text = Text()
        if not self._events:
            text.append("暂无事件")
            return text

        for line in list(self._events)[-max_lines:]:
            text.append(line)
            text.append("\n")
        return text

    def _render_status_bar(self) -> Text:
        text = Text()
        text.append(" h/l ", style="bold cyan")
        text.append("服务  ")
        text.append(" j/k ", style="bold cyan")
        text.append("节点  ")
        text.append(" Enter ", style="bold cyan")
        text.append("切换  ")
        text.append(" d ", style="bold cyan")
        text.append("禁用/启用  ")
        text.append(" q ", style="bold cyan")
        text.append("退出")
        return text

    def _is_selected_service(self, service: ServiceView) -> bool:
        selected_service = self._selected_service()
        return selected_service is service


def build_node_scores(
    nodes: list[str],
    *,
    current_node: Optional[str],
    service_name: str,
    proxy_group_name: str,
    storage: NodeHistoryStorage,
) -> list[NodeScore]:
    scored_nodes = []
    for node in nodes:
        record = storage.get_node_service_record(node, service_name, proxy_group_name)
        disabled = storage.is_node_disabled(node, service_name, proxy_group_name)
        scored_nodes.append(_node_score_from_record(node, record, disabled=disabled))

    return sorted(
        scored_nodes,
        key=lambda node: (node.disabled, -node.score, node.name != current_node, node.name),
    )


def _node_score_from_record(
    node: str,
    record: Optional[ServiceRecord],
    *,
    disabled: bool = False,
) -> NodeScore:
    if record is None:
        return NodeScore(name=node, disabled=disabled)

    return NodeScore(
        name=node,
        score=record.reliability_score,
        status=record.status,
        total_checks=record.total_checks,
        successful_checks=record.successful_checks,
        disabled=disabled,
    )


def _visible_node_window(
    nodes: list[NodeScore],
    selected_index: int,
    max_rows: int,
) -> list[tuple[int, NodeScore]]:
    if max_rows <= 0:
        return []
    if len(nodes) <= max_rows:
        return list(enumerate(nodes))

    selected_index = max(0, min(selected_index, len(nodes) - 1))
    half_window = max_rows // 2
    start = selected_index - half_window
    start = max(0, min(start, len(nodes) - max_rows))
    end = start + max_rows
    return list(enumerate(nodes[start:end], start=start))


def _node_name_style(node: NodeScore, *, selected: bool = False, current: bool = False) -> str:
    if selected:
        return f"bold {_node_status_color(node)} on grey35"
    if current:
        return f"bold {_node_status_color(node)}"
    return _node_status_color(node)


def _node_display_name(node: NodeScore, *, current: bool = False) -> str:
    marker = "* " if current else "  "
    suffix = " [禁用]" if node.disabled else ""
    return f"{marker}{node.name}{suffix}"


def _node_status_color(node: NodeScore) -> str:
    if node.disabled:
        return "bright_black"
    if node.status == "available":
        return "green"
    if node.status == "failed":
        return "red"
    return "white"
