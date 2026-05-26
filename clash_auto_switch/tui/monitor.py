from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, RichLog, Select, Static

from clash_auto_switch.core.clash_api import ProxyGroupState, connection_matches_service
from clash_auto_switch.core.storage import NodeHistoryStorage
from clash_auto_switch.defs import ServiceRecord, ServiceTaskRef


SwitchNodeFunc = Callable[[ServiceTaskRef, str], Awaitable[None]]
DisableNodeFunc = Callable[[ServiceTaskRef, str], Awaitable[None]]
ToggleAutoDetectionFunc = Callable[[ServiceTaskRef, bool], Awaitable[None]]
CheckServiceFunc = Callable[[ServiceTaskRef], Awaitable[None]]
AddTaskFunc = Callable[[str, str], Awaitable[Optional[ServiceTaskRef]]]

MAX_CONNECTION_ROWS = 12


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
class ConnectionRow:
    host: str
    rule: str
    chain: str
    traffic: str
    network: str
    total_bytes: int = 0


@dataclass
class ServiceView:
    task: ServiceTaskRef
    current_node: Optional[str] = None
    last_status: str = "等待检测"
    nodes: list[NodeScore] = field(default_factory=list)
    selected_node_index: int = 0
    connections: list[ConnectionRow] = field(default_factory=list)
    connection_status: str = "等待读取连接"
    rendered_node_signature: tuple[tuple[str, str, str, bool, bool], ...] = ()
    auto_detection_enabled: bool = True


class AddTaskDialog(ModalScreen[Optional[tuple[str, str]]]):
    """Modal dialog for adding a configured service task."""

    CSS = """
    AddTaskDialog {
        align: center middle;
    }

    #add-task-dialog {
        width: 60;
        height: auto;
        border: round cyan;
        padding: 1 2;
        background: $surface;
    }

    #add-task-dialog Select {
        margin-bottom: 1;
    }

    #add-task-actions {
        height: auto;
        align-horizontal: right;
    }
    """

    def __init__(self, proxy_groups: list[str], services: list[str]) -> None:
        super().__init__()
        self.proxy_groups = proxy_groups
        self.services = services

    def compose(self) -> ComposeResult:
        with Vertical(id="add-task-dialog"):
            yield Static("添加任务")
            yield Select(
                [(name, name) for name in self.proxy_groups],
                prompt="ProxyGroup",
                id="add-task-group",
            )
            yield Select(
                [(name, name) for name in self.services],
                prompt="Service",
                id="add-task-service",
            )
            with Horizontal(id="add-task-actions"):
                yield Button("取消", id="add-task-cancel")
                yield Button("添加", id="add-task-confirm", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-task-cancel":
            self.dismiss(None)
            return
        if event.button.id != "add-task-confirm":
            return

        group_value = self.query_one("#add-task-group", Select).value
        service_value = self.query_one("#add-task-service", Select).value
        if group_value == Select.BLANK or service_value == Select.BLANK:
            return
        self.dismiss((str(group_value), str(service_value)))


class MonitorTui(App[None]):
    """Textual terminal UI for monitor mode."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #main {
        height: 1fr;
    }

    #services {
        width: 1fr;
        height: 1fr;
    }

    .service-table {
        width: 1fr;
        height: 1fr;
        border: round #3a3a3a;
    }

    .service-table.selected {
        border: round cyan;
    }

    #side-pane {
        width: 50;
        min-width: 38;
        height: 1fr;
    }

    #connection-pane {
        height: 1fr;
        border: round #444444;
    }

    #connection-title {
        height: 1;
        padding: 0 1;
    }

    #connections {
        height: 1fr;
    }

    #events {
        height: 11;
        border: round #444444;
    }

    #status {
        height: 1;
        padding: 0 1;
        background: #202020;
        color: #b0b0b0;
    }

    #services.events-maximized {
        display: none;
    }

    #connection-pane.events-maximized {
        display: none;
    }

    #side-pane.events-maximized {
        width: 1fr;
    }

    #events.events-maximized {
        height: 1fr;
        border: round cyan;
    }
    """

    BINDINGS = [
        Binding("h", "previous_service", "服务-"),
        Binding("l", "next_service", "服务+"),
        Binding("j", "next_node", "节点+"),
        Binding("k", "previous_node", "节点-"),
        Binding("enter", "switch_node", "切换", priority=True),
        Binding("d", "disable_node", "禁用"),
        Binding("e", "toggle_events", "事件"),
        Binding("a", "toggle_auto_detection", "自动"),
        Binding("c", "check_service", "检测"),
        Binding("t", "add_task", "添加"),
        Binding("q", "quit", "退出"),
    ]

    def __init__(
        self,
        tasks: list[ServiceTaskRef],
        *,
        max_events: int = 500,
        available_proxy_groups: list[str] | None = None,
        available_services: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._service_order = [task.service_name for task in tasks]
        self._services = {task.service_name: ServiceView(task=task) for task in tasks}
        self._events: deque[str] = deque(maxlen=max_events)
        self._selected_service_index: Optional[int] = 0 if tasks else None
        self._switch_node: Optional[SwitchNodeFunc] = None
        self._disable_node: Optional[DisableNodeFunc] = None
        self._toggle_auto_detection: Optional[ToggleAutoDetectionFunc] = None
        self._check_service: Optional[CheckServiceFunc] = None
        self._add_task: Optional[AddTaskFunc] = None
        self._auto_detection_available = False
        self._service_table_ids = {
            task.service_name: f"service-{index}"
            for index, task in enumerate(tasks)
        }
        self._available_proxy_groups = list(available_proxy_groups or [])
        self._available_services = list(available_services or [])
        self._ui_ready = False
        self._events_maximized = False
        self._last_refresh_time: Optional[datetime] = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            with Horizontal(id="services"):
                for task in (service.task for service in self._services.values()):
                    yield DataTable(id=self._service_table_ids[task.service_name], classes="service-table")
            with Vertical(id="side-pane"):
                with Vertical(id="connection-pane"):
                    yield Static("连接: -", id="connection-title")
                    yield DataTable(id="connections")
                yield RichLog(id="events", wrap=True, highlight=False, markup=False, auto_scroll=True)
        yield Static("最近刷新: -", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._ui_ready = True
        for service in self._services.values():
            table = self._service_table(service)
            table.add_columns("节点", "得分", "成功率")
            table.cursor_type = "row"
            table.zebra_stripes = True

        connections = self.query_one("#connections", DataTable)
        connections.add_columns("Host", "Rule", "Chain", "Traffic", "Net")
        connections.cursor_type = "row"
        connections.zebra_stripes = True

        self._render_all()

    def configure_callbacks(
        self,
        switch_node: SwitchNodeFunc,
        disable_node: Optional[DisableNodeFunc] = None,
        toggle_auto_detection: Optional[ToggleAutoDetectionFunc] = None,
        check_service: Optional[CheckServiceFunc] = None,
        add_task: Optional[AddTaskFunc] = None,
    ) -> None:
        self._switch_node = switch_node
        self._disable_node = disable_node
        self._toggle_auto_detection = toggle_auto_detection
        self._check_service = check_service
        self._add_task = add_task
        self._auto_detection_available = toggle_auto_detection is not None
        self._render_status()

    def set_add_task_options(
        self,
        *,
        proxy_groups: list[str] | None = None,
        services: list[str] | None = None,
    ) -> None:
        if proxy_groups is not None:
            self._available_proxy_groups = list(proxy_groups)
        if services is not None:
            self._available_services = list(services)

    def event(self, service_name: str, message: str, *, level: str = "info") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] [{service_name}] {message}"
        self._events.append(line)
        if service_name in self._services:
            self._services[service_name].last_status = message
            self._render_service(self._services[service_name])
        if self._ui_ready:
            self._write_event_line(line)

    def update_service(
        self,
        task: ServiceTaskRef,
        group_state: ProxyGroupState,
        storage: NodeHistoryStorage,
        *,
        disabled_node_names: set[str] | None = None,
    ) -> None:
        service = self._services.get(task.service_name)
        if service is None:
            return

        service.current_node = group_state.now
        service.nodes = build_node_scores(
            group_state.nodes,
            current_node=service.current_node,
            service_name=task.service_name,
            proxy_group_name=task.proxy_group_name,
            storage=storage,
            disabled_node_names=disabled_node_names or set(),
        )
        if service.nodes:
            service.selected_node_index = min(service.selected_node_index, len(service.nodes) - 1)
        else:
            service.selected_node_index = 0
        self._mark_refreshed()
        self._render_service(service)
        if self._is_selected_service(service):
            self._render_connections()

    def update_connections(
        self,
        task: ServiceTaskRef,
        connections_payload: dict[str, Any] | None = None,
        *,
        error: str | None = None,
    ) -> None:
        service = self._services.get(task.service_name)
        if service is None:
            return

        if error is not None:
            service.connection_status = f"读取连接失败: {error}"
            service.connections = []
        else:
            service.connections = build_connection_rows(connections_payload or {}, task.service_name)
            count = len(service.connections)
            service.connection_status = f"{count} 个相关连接" if count else "暂无相关连接"

        self._mark_refreshed()
        if self._is_selected_service(service):
            self._render_connections()

    def selected_task(self) -> Optional[ServiceTaskRef]:
        service = self._selected_service()
        return service.task if service is not None else None

    def set_auto_detection_enabled(self, task: ServiceTaskRef, enabled: bool) -> None:
        service = self._services.get(task.service_name)
        if service is not None:
            service.auto_detection_enabled = enabled
        self._render_status()

    async def add_task_view(self, task: ServiceTaskRef) -> None:
        if task.service_name in self._services:
            return
        self._service_order.append(task.service_name)
        self._services[task.service_name] = ServiceView(task=task)
        self._service_table_ids[task.service_name] = f"service-{len(self._service_table_ids)}"
        if self._selected_service_index is None:
            self._selected_service_index = 0

        if self._ui_ready:
            table = DataTable(
                id=self._service_table_ids[task.service_name],
                classes="service-table",
            )
            table.add_columns("节点", "得分", "成功率")
            table.cursor_type = "row"
            table.zebra_stripes = True
            await self.query_one("#services").mount(table)
            self._render_all()

    def selected_node(self) -> Optional[tuple[ServiceTaskRef, str]]:
        service = self._selected_service()
        if service is None or not service.nodes:
            return None
        node = service.nodes[service.selected_node_index]
        return service.task, node.name

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

    def action_previous_service(self) -> None:
        self._move_service(-1)

    def action_next_service(self) -> None:
        self._move_service(1)

    def action_next_node(self) -> None:
        self._move_node(1)

    def action_previous_node(self) -> None:
        self._move_node(-1)

    async def action_switch_node(self) -> None:
        selection = self.selected_node()
        if selection is None:
            self.event("system", "没有可切换的节点")
            return
        if self._switch_node is None:
            self.event("system", "当前模式不支持手动切换")
            return

        task, node_name = selection
        try:
            await self._switch_node(task, node_name)
        except Exception as exc:
            self.event(task.service_name, f"手动切换失败 | {exc}")

    async def action_disable_node(self) -> None:
        selection = self.selected_node()
        if selection is None:
            self.event("system", "没有可切换禁用状态的节点")
            return
        if self._disable_node is None:
            self.event("system", "当前模式不支持禁用节点")
            return

        task, node_name = selection
        try:
            await self._disable_node(task, node_name)
        except Exception as exc:
            self.event(task.service_name, f"切换禁用状态失败 | {exc}")

    def action_quit(self) -> None:
        self.event("system", "退出")
        self.exit()

    def action_toggle_events(self) -> None:
        self._events_maximized = not self._events_maximized
        if self._ui_ready:
            self._apply_events_layout()
            if self._events_maximized:
                self.query_one("#events", RichLog).focus()

    async def action_toggle_auto_detection(self) -> None:
        if self._toggle_auto_detection is None:
            self.event("system", "当前模式不支持自动检测开关")
            return

        service = self._selected_service()
        if service is None:
            self.event("system", "没有可切换自动检测的服务")
            return

        next_enabled = not service.auto_detection_enabled
        service.auto_detection_enabled = next_enabled
        self._render_status()
        try:
            await self._toggle_auto_detection(service.task, next_enabled)
        except Exception as exc:
            service.auto_detection_enabled = not next_enabled
            self._render_status()
            self.event("system", f"切换自动检测失败 | {exc}")

    async def action_check_service(self) -> None:
        task = self.selected_task()
        if task is None:
            self.event("system", "没有可检测的服务")
            return
        if self._check_service is None:
            self.event("system", "当前模式不支持手动检测")
            return

        try:
            await self._check_service(task)
        except Exception as exc:
            self.event(task.service_name, f"手动检测失败 | {exc}")

    def action_add_task(self) -> None:
        if self._add_task is None:
            self.event("system", "当前模式不支持添加任务")
            return
        if not self._available_proxy_groups:
            self.event("system", "没有可添加的 ProxyGroup")
            return
        if not self._available_services:
            self.event("system", "没有可添加的服务")
            return

        self.push_screen(
            AddTaskDialog(self._available_proxy_groups, self._available_services),
            callback=self._handle_add_task_selection,
        )

    async def _handle_add_task_selection(self, selected: Optional[tuple[str, str]]) -> None:
        if selected is None:
            return

        proxy_group_name, service_name = selected
        try:
            task = await self._add_task(proxy_group_name, service_name)
        except Exception as exc:
            self.event("system", f"添加任务失败 | {exc}")
            return
        if task is None:
            return
        await self.add_task_view(task)
        self.event(task.service_name, f"添加任务 | {task.proxy_group_name}")

    def _move_service(self, delta: int) -> None:
        if not self._service_order:
            return
        if self._selected_service_index is None:
            self._selected_service_index = 0 if delta >= 0 else len(self._service_order) - 1
        else:
            self._selected_service_index = (self._selected_service_index + delta) % len(self._service_order)
        self._render_all()

    def _move_node(self, delta: int) -> None:
        if self._selected_service_index is None:
            self._selected_service_index = 0
        service = self._selected_service()
        if service is None or not service.nodes:
            return
        service.selected_node_index = (service.selected_node_index + delta) % len(service.nodes)
        self._render_service(service)

    def _selected_service(self) -> Optional[ServiceView]:
        if not self._service_order or self._selected_service_index is None:
            return None
        service_name = self._service_order[self._selected_service_index]
        return self._services.get(service_name)

    def _render_all(self) -> None:
        if not self._ui_ready:
            return
        self._apply_events_layout()
        for service in self._services.values():
            self._render_service(service)
        self._render_connections()
        events = self.query_one("#events", RichLog)
        events.clear()
        for line in self._events:
            self._write_event_line(line)
        self._render_status()

    def _write_event_line(self, line: str) -> None:
        self.query_one("#events", RichLog).write(Text(line))

    def _render_service(self, service: ServiceView) -> None:
        if not self._ui_ready:
            return

        table = self._service_table(service)
        table.border_title = escape(f"{service.task.service_name} | {service.task.proxy_group_name}")
        current_node = service.current_node or "-"
        table.border_subtitle = escape(f"当前: {current_node} | {service.last_status}")
        table.set_class(self._is_selected_service(service), "selected")

        if not service.nodes:
            signature = (("等待读取 ProxyGroup 节点", "-", "-", False, False),)
            if service.rendered_node_signature != signature:
                table.clear()
                table.add_row("等待读取 ProxyGroup 节点", "-", "-")
                service.rendered_node_signature = signature
            return

        signature = _node_table_signature(service.nodes, service.current_node)
        if service.rendered_node_signature != signature:
            table.clear()
            for index, (node_name, score, success_rate, is_current, is_disabled) in enumerate(signature):
                current_marker = "* " if is_current else "  "
                disabled_marker = " [禁用]" if is_disabled else ""
                table.add_row(
                    f"{current_marker}{node_name}{disabled_marker}",
                    score,
                    success_rate,
                    key=str(index),
                )
            service.rendered_node_signature = signature
        table.move_cursor(row=service.selected_node_index, column=0, animate=False)

    def _render_connections(self) -> None:
        if not self._ui_ready:
            return

        selected = self._selected_service()
        title = self.query_one("#connection-title", Static)
        table = self.query_one("#connections", DataTable)
        table.clear()

        if selected is None:
            title.update("连接: -")
            table.add_row("-", "-", "-", "-", "-")
            return

        title.update(f"连接: {selected.task.service_name} | {selected.connection_status}")
        if not selected.connections:
            table.add_row(selected.connection_status, "-", "-", "-", "-")
            return

        for row in selected.connections:
            table.add_row(row.host, row.rule, row.chain, row.traffic, row.network)

    def _service_table(self, service: ServiceView) -> DataTable:
        return self.query_one(f"#{self._service_table_ids[service.task.service_name]}", DataTable)

    def _is_selected_service(self, service: ServiceView) -> bool:
        return self._selected_service() is service

    def _apply_events_layout(self) -> None:
        self.query_one("#services").set_class(self._events_maximized, "events-maximized")
        self.query_one("#side-pane").set_class(self._events_maximized, "events-maximized")
        self.query_one("#connection-pane").set_class(self._events_maximized, "events-maximized")
        self.query_one("#events", RichLog).set_class(self._events_maximized, "events-maximized")

    def _mark_refreshed(self) -> None:
        self._last_refresh_time = datetime.now()
        self._render_status()

    def _render_status(self) -> None:
        if not self._ui_ready:
            return
        refresh_text = self._last_refresh_time.strftime("%H:%M:%S") if self._last_refresh_time else "-"
        selected = self._selected_service()
        selected_text = selected.task.service_name if selected is not None else "-"
        if self._auto_detection_available:
            auto_enabled = selected.auto_detection_enabled if selected is not None else True
            auto_text = "开" if auto_enabled else "关"
            auto_style = "bold green" if auto_enabled else "bold red"
        else:
            auto_text = "不可用"
            auto_style = "bold yellow"

        status = Text()
        status.append("最近刷新: ", style="dim")
        status.append(refresh_text, style="bold")
        status.append("  |  ", style="dim")
        status.append("当前服务: ", style="dim")
        status.append(selected_text, style="bold cyan")
        status.append("  |  ", style="dim")
        status.append("自动检测: ", style="dim")
        status.append(auto_text, style=auto_style)
        self.query_one("#status", Static).update(status)


def build_node_scores(
    nodes: list[str],
    *,
    current_node: Optional[str],
    service_name: str,
    proxy_group_name: str,
    storage: NodeHistoryStorage,
    disabled_node_names: set[str] | None = None,
) -> list[NodeScore]:
    scored_nodes = []
    disabled_node_names = disabled_node_names or set()
    for node in nodes:
        record = storage.get_node_service_record(node, service_name, proxy_group_name)
        scored_nodes.append(
            _node_score_from_record(
                node,
                record,
                disabled=node in disabled_node_names,
            )
        )

    return sorted(
        scored_nodes,
        key=lambda node: (node.disabled, -node.score, node.name != current_node, node.name),
    )


def build_connection_rows(
    connections_payload: dict[str, Any],
    service_name: str,
    *,
    limit: int = MAX_CONNECTION_ROWS,
) -> list[ConnectionRow]:
    connections = connections_payload.get("connections") or []
    if not isinstance(connections, list):
        return []

    rows = [
        _connection_row(connection)
        for connection in connections
        if isinstance(connection, dict) and connection_matches_service(connection, service_name)
    ]
    rows.sort(key=lambda row: (-row.total_bytes, row.host))
    return rows[:limit]


def _node_table_signature(
    nodes: list[NodeScore],
    current_node: Optional[str],
) -> tuple[tuple[str, str, str, bool, bool], ...]:
    return tuple(
        (
            node.name,
            f"{node.score:.3f}",
            f"{node.success_rate:.0%}" if node.total_checks else "-",
            node.name == current_node,
            node.disabled,
        )
        for node in nodes
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


def _node_display_name(node: NodeScore, *, current: bool = False) -> str:
    marker = "* " if current else "  "
    return f"{marker}{node.name}"


def _connection_row(connection: dict[str, Any]) -> ConnectionRow:
    upload = _int_value(connection.get("upload"))
    download = _int_value(connection.get("download"))
    total = upload + download
    return ConnectionRow(
        host=_connection_host(connection),
        rule=_connection_rule(connection),
        chain=_connection_chain(connection),
        traffic=f"U {_format_bytes(upload)} / D {_format_bytes(download)}",
        network=_connection_network(connection),
        total_bytes=total,
    )


def _connection_host(connection: dict[str, Any]) -> str:
    metadata = connection.get("metadata")
    if isinstance(metadata, dict):
        host = metadata.get("host")
        if isinstance(host, str) and host:
            return _truncate(host, 32)
        destination_ip = metadata.get("destinationIP")
        destination_port = metadata.get("destinationPort")
        if destination_ip:
            return _truncate(f"{destination_ip}:{destination_port or '-'}", 32)

    host = connection.get("host")
    if isinstance(host, str) and host:
        return _truncate(host, 32)
    return "-"


def _connection_rule(connection: dict[str, Any]) -> str:
    rule = connection.get("rule")
    payload = connection.get("rulePayload")
    parts = [str(part) for part in (rule, payload) if part]
    return _truncate(" ".join(parts), 32) if parts else "-"


def _connection_chain(connection: dict[str, Any]) -> str:
    chains = connection.get("chains")
    if isinstance(chains, list):
        values = [str(chain) for chain in chains if chain]
        if values:
            return _truncate(" > ".join(values), 28)
    chain = connection.get("chain")
    if isinstance(chain, str) and chain:
        return _truncate(chain, 28)
    return "-"


def _connection_network(connection: dict[str, Any]) -> str:
    metadata = connection.get("metadata")
    if isinstance(metadata, dict):
        network = metadata.get("network") or metadata.get("type")
        if network:
            return str(network)
    network = connection.get("network")
    return str(network) if network else "-"


def _format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    amount = float(value)
    for unit in units:
        if amount < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)}B"
            return f"{amount:.1f}{unit}"
        amount /= 1024.0
    return f"{value}B"


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    return 0


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    if max_length <= 3:
        return value[:max_length]
    return f"{value[: max_length - 3]}..."
