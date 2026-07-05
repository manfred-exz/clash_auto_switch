# 节点连通性标记与后台刷新

## 目标
- TUI 节点条目为连通性有问题的节点添加标记
- 默认每 5 分钟后台刷新所有节点连通性

## 改动点

### 1. `tui/monitor.py`
- `NodeScore` 新增 `connectivity_ok: Optional[bool] = None`
- `MonitorTui`:
  - 新增 `_connectivity_by_service: dict[str, dict[str, bool]]` 缓存
  - `update_service` 构建 nodes 后调用 `_apply_connectivity(service)` 把缓存状态回填到 NodeScore
  - 新增 `update_connectivity(task, node_status: dict[str, bool])`：合并缓存 + 更新当前 ServiceView.nodes + 重新渲染
  - `_render_service`：对 `connectivity_ok is False` 的节点在名称前加 `! ` 标记
  - `_node_table_signature`：tuple 增加一项 `connectivity_state`（"ok"/"fail"/"unknown"）以触发重渲染

### 2. `auto_monitor.py`
- 新增常量 `CONNECTIVITY_REFRESH_INTERVAL_SEC = 300.0`
- `run_background_tasks` 增加 `connectivity_refresh_loop` 任务
- `refresh_all_connectivity`：遍历每个 task，并发对 group 节点调用 `clash.get_proxy_delay`，按返回 `delay` 是否为非负数判断 ok，聚合后调用 `tui.update_connectivity`，发一条汇总事件

## 验证
- `python -m py_compile` 受影响文件
- `python -m unittest tests.test_proxy_switcher`
