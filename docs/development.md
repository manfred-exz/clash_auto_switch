# Development Notes

This project is organized around three layers:

- `clash_auto_switch/core/`: low-level Clash API access, cached Clash state, service probes, connection cleanup, proxy switching, and node history storage.
- `clash_auto_switch/tui/`: terminal UI rendering and keyboard input.
- `clash_auto_switch/auto_monitor.py` and `clash_auto_switch/monitor.py`: orchestration runners.

The orchestration code should stay in runner classes. Avoid adding long nested functions to entry points or low-level modules.

## Main Runtime Objects

### `ClashClient`

`core/clash_api.py` is a thin async REST client for Clash-compatible controllers. It should not cache state or make policy decisions.

Use it for direct API calls:

- `get_proxies()`
- `get_proxy(name)`
- `select_proxy(group, node)`
- `iter_logs(level="info")`
- connection APIs

### `ClashProxyState`

`core/clash_state.py` wraps `ClashClient` and owns cached proxy state.

Use this from monitor code instead of calling `ClashClient` directly when reading ProxyGroup state. It refreshes stale values automatically and invalidates after selection changes.

Typical usage:

```python
async with ClashClient.from_external_controller(controller, secret=secret) as client:
    clash = ClashProxyState(client)
    group = await clash.get_proxy_group("Youtube")
    await clash.select_proxy("Youtube", "node-a")
```

### `AutoMonitorRunner`

`auto_monitor.AutoMonitorRunner` drives auto mode from Clash realtime logs.

Responsibilities:

- Build enabled auto-trigger tasks from config.
- Consume `ClashProxyState.iter_logs()`.
- Match log destinations to services.
- Run availability checks only when matching traffic is observed.
- Switch nodes through `switch_until_service_available()`.
- Handle TUI manual switching and node disabling.
- Refresh TUI state periodically.

### `PeriodicMonitorRunner`

`monitor.PeriodicMonitorRunner` runs checks on a fixed interval.

Responsibilities:

- Start one periodic task per enabled config task.
- Re-check and switch until available for `run-once`.
- Respect disabled nodes from config.
- Handle TUI manual switching and node disabling.

## TUI Data Flow

`MonitorTui` is a Textual app. It should not call Clash APIs. It only renders state passed by runners.

The intended flow is:

1. Runner asks `ClashProxyState` for a `ProxyGroupState`.
2. Runner calls `MonitorTui.update_service(...)`.
3. TUI builds visible node rows from:
   - ProxyGroup node list
   - current node from Clash
   - node history records
   - disabled node names from config

Disabled nodes are shown at the end of the visible list with a disabled marker, but are skipped by automatic switching.

Connection display follows the same rule:

1. Runner calls `ClashProxyState.get_connections()`.
2. Runner passes the payload or error to `MonitorTui.update_connections(...)`.
3. TUI filters and formats connections for the currently selected service using service host patterns.

The TUI owns keyboard state, selected service, selected node, and Textual widgets. Runners own Clash API calls, service checks, switching, disabling, and connection refresh scheduling.

Auto mode also writes structured diagnostics to `diagnostics.jsonl` under `get_data_directory()`. Use this file when investigating unexpected checks or switches; it records trigger logs, current node snapshots, check start/end, connection snapshots, and TUI-visible events.

## Configuration

User configuration is represented by dataclasses in `defs.py` and parsed in `config.py`.

Important fields:

- `clash`: Clash controller and probe proxy settings.
- `monitoring`: periodic mode behavior.
- `tasks`: enabled service/proxy group pairs.
- `disabled_nodes`: user-disabled nodes, stored in config rather than history.

Node history belongs in `NodeHistoryStorage`; user policy belongs in config.

## Adding a Service Checker

1. Add an async checker in `core/service_tester.py` returning `TestResultItem`.
2. Add aliases to `SERVICE_ALIASES`.
3. Register the checker in `SERVICE_CHECKERS`.
4. For auto mode, add trigger host patterns in `core/service_hosts.py`.

Keep checkers conservative: return `Yes` only when the service is actually usable.

## Adding Auto Trigger Hosts

Add hosts to `SERVICE_HOST_PATTERNS` in `core/service_hosts.py`.

Patterns are substring matches against the parsed destination host and raw log payload. Prefer specific hostnames to broad domains when two services share infrastructure, for example YouTube Premium and YouTube Music.

## Manual Node Disable

The TUI `d` key toggles disabled state for the currently selected node for exactly one `(proxy_group_name, service_name)` pair.

Disabled nodes are saved to `config.json`:

```json
"disabled_nodes": [
  {
    "proxy_group_name": "Youtube",
    "service_name": "youtube_music",
    "node_name": "node-a"
  }
]
```

Disabled nodes are shown at the end of the TUI node list and skipped by automatic switching.

## Verification

Useful commands:

```powershell
uv run python -m py_compile clash_auto_switch\auto_monitor.py clash_auto_switch\monitor.py clash_auto_switch\entry.py
uv run python -m unittest discover -s tests
uv run python -m clash_auto_switch --help
uv run python -m clash_auto_switch.core.service_tester --service youtube_music --debug
```

When debugging live switching, prefer `debug-switch` before running the TUI:

```powershell
uv run python -m clash_auto_switch debug-switch Youtube youtube_music
```
