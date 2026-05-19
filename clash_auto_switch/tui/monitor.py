from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, RichLog, Static

from clash_auto_switch.core.clash_state import ProxyGroupState
from clash_auto_switch.core.connections import connection_matches_service
from clash_auto_switch.core.storage import NodeHistoryStorage
from clash_auto_switch.defs import ProxyServicePair, ServiceRecord


SwitchNodeFunc = Callable[[ProxyServicePair, str], Awaitable[None]]
DisableNodeFunc = Callable[[ProxyServicePair, str], Awaitable[None]]

MAX_CONNECTION_ROWS = 12


@dataclass
class NodeScore:
    name: str
    score: float = 0.0
    status: str = "unknown"
    total_checks: int = 0
    successful_checks: int = 0

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
    task: ProxyServicePair
    current_node: Optional[str] = None
    last_status: str = "等待检测"
    nodes: list[NodeScore] = field(default_factory=list)
    selected_node_index: int = 0
    connections: list[ConnectionRow] = field(default_factory=list)
    connection_status: str = "等待读取连接"


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
        border: round cyan;
    }

    .service-table.selected {
        border: round magenta;
    }

    #connection-pane {
        width: 48;
        min-width: 36;
        height: 1fr;
        border: round yellow;
    }

    #connection-title {
        height: 1;
        padding: 0 1;
    }

    #connections {
        height: 1fr;
    }

    #events {
        height: 7;
        border: round green;
    }
    """

    BINDINGS = [
        ("h", "previous_service", "服务-"),
        ("l", "next_service", "服务+"),
        ("j", "next_node", "节点+"),
        ("k", "previous_node", "节点-"),
        ("enter", "switch_node", "切换"),
        ("d", "disable_node", "禁用"),
        ("q", "quit", "退出"),
    ]

    def __init__(self, tasks: list[ProxyServicePair], *, max_events: int = 50) -> None:
        super().__init__()
        self._service_order = [task.service_name for task in tasks]
        self._services = {task.service_name: ServiceView(task=task) for task in tasks}
        self._events: deque[str] = deque(maxlen=max_events)
        self._selected_service_index: Optional[int] = 0 if tasks else None
        self._switch_node: Optional[SwitchNodeFunc] = None
        self._disable_node: Optional[DisableNodeFunc] = None
        self._service_table_ids = {
            task.service_name: f"service-{index}"
            for index, task in enumerate(tasks)
        }
        self._ui_ready = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            with Horizontal(id="services"):
                for task in (service.task for service in self._services.values()):
                    yield DataTable(id=self._service_table_ids[task.service_name], classes="service-table")
            with Vertical(id="connection-pane"):
                yield Static("连接: -", id="connection-title")
                yield DataTable(id="connections")
        yield RichLog(id="events", wrap=True, highlight=False, markup=False)
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
    ) -> None:
        self._switch_node = switch_node
        self._disable_node = disable_node

    def event(self, service_name: str, message: str, *, level: str = "info") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] [{service_name}] {message}"
        self._events.append(line)
        if service_name in self._services:
            self._services[service_name].last_status = message
            self._render_service(self._services[service_name])
        if self._ui_ready:
            self.query_one("#events", RichLog).write(line)

    def update_service(
        self,
        task: ProxyServicePair,
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
        self._render_service(service)
        if self._is_selected_service(service):
            self._render_connections()

    def update_connections(
        self,
        task: ProxyServicePair,
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

        if self._is_selected_service(service):
            self._render_connections()

    def selected_task(self) -> Optional[ProxyServicePair]:
        service = self._selected_service()
        return service.task if service is not None else None

    def selected_node(self) -> Optional[tuple[ProxyServicePair, str]]:
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
            self.event("system", "没有可禁用的节点")
            return
        if self._disable_node is None:
            self.event("system", "当前模式不支持禁用节点")
            return

        task, node_name = selection
        try:
            await self._disable_node(task, node_name)
        except Exception as exc:
            self.event(task.service_name, f"禁用节点失败 | {exc}")

    def action_quit(self) -> None:
        self.event("system", "退出")
        self.exit()

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
        for service in self._services.values():
            self._render_service(service)
        self._render_connections()
        events = self.query_one("#events", RichLog)
        events.clear()
        for line in self._events:
            events.write(line)

    def _render_service(self, service: ServiceView) -> None:
        if not self._ui_ready:
            return

        table = self._service_table(service)
        table.clear()
        table.border_title = f"{service.task.service_name} | {service.task.proxy_group_name}"
        current_node = service.current_node or "-"
        table.border_subtitle = f"当前: {current_node} | {service.last_status}"
        table.set_class(self._is_selected_service(service), "selected")

        if not service.nodes:
            table.add_row("等待读取 ProxyGroup 节点", "-", "-")
            return

        for index, node in enumerate(service.nodes):
            current_marker = "* " if node.name == service.current_node else "  "
            table.add_row(
                f"{current_marker}{node.name}",
                f"{node.score:.3f}",
                f"{node.success_rate:.0%}" if node.total_checks else "-",
                key=str(index),
            )
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
        if node in disabled_node_names:
            continue
        record = storage.get_node_service_record(node, service_name, proxy_group_name)
        scored_nodes.append(_node_score_from_record(node, record))

    return sorted(
        scored_nodes,
        key=lambda node: (-node.score, node.name != current_node, node.name),
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


def _node_score_from_record(
    node: str,
    record: Optional[ServiceRecord],
) -> NodeScore:
    if record is None:
        return NodeScore(name=node)

    return NodeScore(
        name=node,
        score=record.reliability_score,
        status=record.status,
        total_checks=record.total_checks,
        successful_checks=record.successful_checks,
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
