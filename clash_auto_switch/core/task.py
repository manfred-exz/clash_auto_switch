from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import httpx

from clash_auto_switch.app_context import AppContext
from clash_auto_switch.config import save_app_config
from clash_auto_switch.core.services.common import (
    check_proxy_connectivity,
    service_debug_event_handler,
)
from clash_auto_switch.core.services.probe import probe_service
from clash_auto_switch.defs import DisabledNode
from clash_auto_switch.defs import ProxyServicePair


EventFunc = Callable[[str, str], None]
UNTESTED_NODE_SCORE = 0.5


@dataclass(frozen=True)
class ProxyCandidate:
    name: str
    score: float
    status: str = "untested"
    total_checks: int = 0
    successful_checks: int = 0
    is_current: bool = False
    is_alive: bool = True


@dataclass(frozen=True)
class SwitchAttemptResult:
    ok: bool
    switched: bool
    attempts: int


@dataclass(frozen=True)
class NodeProbeResult:
    ok: bool
    status_text: str
    connectivity_ok: bool
    connectivity_status: str
    recorded: bool


@dataclass(frozen=True)
class ServiceTask:
    """Runtime behavior for one service plus one Clash proxy group."""

    pair: ProxyServicePair
    app: AppContext

    @classmethod
    def from_pair(cls, pair: ProxyServicePair, app: AppContext | None = None) -> "ServiceTask":
        return cls(pair=pair, app=app or AppContext.current())

    @property
    def proxy_group_name(self) -> str:
        return self.pair.proxy_group_name

    @property
    def service_name(self) -> str:
        return self.pair.service_name

    @property
    def enabled(self) -> bool:
        return self.pair.enabled

    async def list_alive_proxy_candidates(self) -> list[ProxyCandidate]:
        """Build switch candidates from every alive node in this task's proxy group."""
        clash = self.app.clash
        disabled_node_names = self.disabled_node_names()
        group_info = await clash.get_proxy(self.proxy_group_name)
        candidates = group_info.get("all") or []
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(f"Proxy group '{self.proxy_group_name}' has no candidates in 'all'")

        current = group_info.get("now")
        switch_candidates = []
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue

            is_alive = True
            try:
                candidate_info = await clash.get_proxy(candidate)
                is_alive = candidate_info.get("alive") is not False
            except httpx.HTTPError:
                is_alive = True

            if not is_alive or candidate in disabled_node_names:
                continue

            record = self.app.storage.get_node_service_record(
                candidate,
                self.service_name,
                self.proxy_group_name,
            )
            if record is None:
                switch_candidates.append(
                    ProxyCandidate(
                        name=candidate,
                        score=UNTESTED_NODE_SCORE,
                        is_current=candidate == current,
                        is_alive=True,
                    )
                )
                continue

            switch_candidates.append(
                ProxyCandidate(
                    name=candidate,
                    score=record.reliability_score,
                    status=record.status,
                    total_checks=record.total_checks,
                    successful_checks=record.successful_checks,
                    is_current=candidate == current,
                    is_alive=True,
                )
            )

        return sorted(
            switch_candidates,
            key=lambda candidate: (-candidate.score, candidate.is_current, candidate.name),
        )

    async def switch_to_next_ranked_proxy(
        self,
        *,
        event_handler: Optional[EventFunc] = None,
    ) -> ProxyCandidate:
        candidates = await self.list_alive_proxy_candidates()
        if not candidates:
            raise RuntimeError(f"No alive proxies found in group '{self.proxy_group_name}'.")

        proxy_to_try = next((item for item in candidates if not item.is_current), None)
        if proxy_to_try is None:
            raise RuntimeError(f"No suitable proxy found in group '{self.proxy_group_name}'.")

        await self.switch_to_node(proxy_to_try.name)
        if event_handler is not None:
            event_handler(
                self.service_name,
                f"尝试节点: {proxy_to_try.name} | 历史评分: {proxy_to_try.score:.3f}",
            )
        return proxy_to_try

    def disabled_node_names(self) -> set[str]:
        return {
            node.node_name
            for node in self.app.config.disabled_nodes
            if node.proxy_group_name == self.proxy_group_name
            and node.service_name == self.service_name
        }

    def toggle_node_disabled(self, node_name: str) -> bool:
        before_count = len(self.app.config.disabled_nodes)
        self.app.config.disabled_nodes = [
            node
            for node in self.app.config.disabled_nodes
            if not (
                node.proxy_group_name == self.proxy_group_name
                and node.service_name == self.service_name
                and node.node_name == node_name
            )
        ]
        if len(self.app.config.disabled_nodes) == before_count:
            self.app.config.disabled_nodes.append(
                DisabledNode(
                    proxy_group_name=self.proxy_group_name,
                    service_name=self.service_name,
                    node_name=node_name,
                )
            )
        return save_app_config(self.app.config)

    async def current_node(self) -> Optional[str]:
        clash = self.app.clash
        try:
            group_state = await clash.get_proxy_group(self.proxy_group_name)
        except Exception as exc:
            self.app.diagnostics.write(
                "current_node_read_failed",
                service_name=self.service_name,
                proxy_group_name=self.proxy_group_name,
                error=str(exc),
            )
            return None
        return group_state.now

    async def switch_to_node(self, node_name: str) -> None:
        clash = self.app.clash
        await clash.select_proxy(self.proxy_group_name, node_name)
        verified_group_info = await clash.get_proxy(self.proxy_group_name)
        verified_current = verified_group_info.get("now")
        if verified_current != node_name:
            raise RuntimeError(
                f"Proxy group '{self.proxy_group_name}' switch verification failed: "
                f"expected '{node_name}', got '{verified_current}'"
            )

    async def close_connections_best_effort(
        self,
        event_handler: Optional[EventFunc] = None,
    ) -> int:
        clash = self.app.clash
        try:
            closed_count = await clash.close_service_connections(self.service_name)
        except Exception as exc:
            closed_count = 0
            if event_handler is not None:
                event_handler(self.service_name, f"关闭连接失败 | {exc}")

        if event_handler is not None:
            event_handler(self.service_name, f"关闭连接 | {closed_count} 个")
        return closed_count

    async def probe_current_node_once(
        self,
        *,
        event_handler: Optional[EventFunc] = None,
    ) -> NodeProbeResult:
        current_node = await self.current_node()

        connectivity_ok, connectivity_status = await check_proxy_connectivity(
            self.app.config.clash.http_proxy,
        )
        if not connectivity_ok:
            node_display = current_node if current_node else "未知"
            if event_handler is not None:
                event_handler(self.service_name, f"节点连通性失败 | {connectivity_status} | 跳过节点: {node_display}")
            return NodeProbeResult(
                ok=False,
                status_text=connectivity_status,
                connectivity_ok=False,
                connectivity_status=connectivity_status,
                recorded=False,
            )

        try:
            with service_debug_event_handler(event_handler):
                ok, status_text = await probe_service(self.service_name, self.app.config.clash.http_proxy)
        except Exception as exc:
            ok, status_text = False, f"检测异常: {exc}"

        if isinstance(current_node, str) and current_node:
            self.app.storage.record_node_status(
                node_name=current_node,
                service_name=self.service_name,
                proxy_group=self.proxy_group_name,
                is_available=ok,
            )

        return NodeProbeResult(
            ok=ok,
            status_text=status_text,
            connectivity_ok=True,
            connectivity_status=connectivity_status,
            recorded=isinstance(current_node, str) and bool(current_node),
        )

    async def switch_until_available(
        self,
        *,
        event_handler: Optional[EventFunc] = None,
        after_switch: Optional[Callable[[str], Awaitable[None]]] = None,
        max_attempts: Optional[int] = None,
    ) -> SwitchAttemptResult:
        attempts = 0
        switched_any = False
        tried_nodes: set[str] = set()

        while True:
            attempts += 1
            ok, switched = await self._probe_and_switch_if_unavailable(
                event_handler=event_handler,
            )
            switched_any = switched_any or switched

            if ok or not switched:
                return SwitchAttemptResult(ok=ok, switched=switched_any, attempts=attempts)

            current_node = await self.current_node()
            if after_switch is not None and isinstance(current_node, str) and current_node:
                await after_switch(current_node)

            if max_attempts is not None and attempts >= max_attempts:
                if event_handler is not None:
                    event_handler(self.service_name, f"停止尝试 | 已达到最大尝试次数 {max_attempts}")
                return SwitchAttemptResult(ok=False, switched=switched_any, attempts=attempts)

            if not isinstance(current_node, str) or not current_node:
                return SwitchAttemptResult(ok=False, switched=switched_any, attempts=attempts)

            if current_node in tried_nodes:
                if event_handler is not None:
                    event_handler(self.service_name, f"停止尝试 | 节点重复: {current_node}")
                return SwitchAttemptResult(ok=False, switched=switched_any, attempts=attempts)
            tried_nodes.add(current_node)

    async def _probe_and_switch_if_unavailable(
        self,
        *,
        event_handler: Optional[EventFunc] = None,
    ) -> tuple[bool, bool]:
        current_node = await self.current_node()

        connectivity_ok, connectivity_status = await check_proxy_connectivity(
            self.app.config.clash.http_proxy,
        )
        if not connectivity_ok:
            node_display = current_node if current_node else "未知"
            if event_handler is not None:
                event_handler(self.service_name, f"节点连通性失败 | {connectivity_status} | 跳过节点: {node_display}")
            return False, await self._switch_to_next_available_proxy(event_handler=event_handler)

        try:
            with service_debug_event_handler(event_handler):
                ok, status_text = await probe_service(self.service_name, self.app.config.clash.http_proxy)
        except Exception as exc:
            ok, status_text = False, f"检测异常: {exc}"

        if isinstance(current_node, str) and current_node:
            self.app.storage.record_node_status(
                node_name=current_node,
                service_name=self.service_name,
                proxy_group=self.proxy_group_name,
                is_available=ok,
            )

        node_display = current_node if current_node else "未知"
        if ok:
            if event_handler is not None:
                event_handler(self.service_name, f"服务可用 | {status_text} | 节点: {node_display}")
            return True, False

        if event_handler is not None:
            event_handler(self.service_name, f"服务不可用 | {status_text} | 节点: {node_display}")
        return False, await self._switch_to_next_available_proxy(event_handler=event_handler)

    async def _switch_to_next_available_proxy(
        self,
        *,
        event_handler: Optional[EventFunc] = None,
    ) -> bool:
        try:
            proxy_to_try = await self.switch_to_next_ranked_proxy(event_handler=event_handler)
            if event_handler is not None:
                event_handler(self.service_name, f"切换代理 | {self.proxy_group_name} -> {proxy_to_try.name}")
            return True
        except Exception as exc:
            if event_handler is not None:
                event_handler(self.service_name, f"切换失败 | {exc}")
            return False
