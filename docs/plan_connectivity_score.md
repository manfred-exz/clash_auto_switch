# 连通性重试 + 双评分

## 问题
check_proxy_connectivity 失败不计入评分，导致间歇断连节点被反复切换。

## 改动

### 1. `services/common.py` — 连通性重试
`check_proxy_connectivity` 失败时重试，默认 3 次，间隔 1s。返回结果带尝试次数。

### 2. `defs.py` — 新增 NodeConnectivityRecord
```python
@dataclass
class NodeConnectivityRecord:
    score: float = 1.0
    total_checks: int = 0
    successful_checks: int = 0
    last_check_time: float = 0.0
```

### 3. `storage.py` — 连通性评分存储
- `_connectivity_records: Dict[str, NodeConnectivityRecord]`
- `record_node_connectivity(node_name, is_ok)` — EMA 更新 score
- `get_node_connectivity(node_name) -> Optional[NodeConnectivityRecord]`
- 持久化：新文件格式 `{"service_records": {...}, "connectivity_records": {...}}`，加载兼容旧格式

### 4. `task.py` — 双评分排名 + 记录连通性
- `ProxyCandidate` 增 `connectivity_score: float = 1.0`
- `list_alive_proxy_candidates`: 查 connectivity record，combined = service_score * connectivity_score，按 -combined 排序
- 两处 check_proxy_connectivity 调用后: `storage.record_node_connectivity(node, ok)`

### 5. `auto_monitor.py` — 后台刷新也记录连通性
`_probe_single_node` 结果写入 storage，使评分能随后台刷新恢复

### 6. `tui/monitor.py` — 显示连通性评分
- `NodeScore` 增 `connectivity_score: Optional[float] = None`
- `build_node_scores` 查 connectivity record 填充
- 表格增 "连通" 列；signature 增 connectivity_score

## 验证
py_compile + unittest
