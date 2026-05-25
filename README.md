# clash-auto-switch

`clash-auto-switch` 是一个基于 Clash API 的服务可用性监控和自动切换工具。它会在指定 ProxyGroup 内切换节点，直到目标服务可用。

适合这些场景：

- YouTube Music、ChatGPT、Gemini、Claude 等服务在部分节点不可用
- 节点可连通但服务解锁状态不稳定
- 希望在终端 TUI 中查看每个服务的节点得分、成功率和当前节点
- 希望根据 Clash 实时连接日志，只在用户访问相关服务时触发检测和切换

![TUI](./images/1.jpg)

## Quickstart

### 1. 安装

源码运行推荐使用 `uv`：

```powershell
uv sync
```

也可以安装为命令行工具：

```powershell
pip install .
```

安装后命令名是：

```powershell
clash-auto-switch --help
```

源码目录中也可以直接运行：

```powershell
uv run python -m clash_auto_switch --help
```

### 2. 生成配置

```powershell
uv run python -m clash_auto_switch generate-config
```

查看配置文件路径：

```powershell
uv run python -m clash_auto_switch show-config
```

配置文件默认位置：

- Windows: `%APPDATA%\clash-auto-switch\config.json`
- macOS: `~/Library/Application Support/clash-auto-switch/config.json`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/clash-auto-switch/config.json`

### 3. 编辑配置

最小示例：

```json
{
  "clash": {
    "controller": "127.0.0.1:9097",
    "secret": null,
    "http_proxy": "http://127.0.0.1:7890"
  },
  "monitoring": {
    "interval_sec": 300.0,
    "max_rotations": 0,
    "once": false
  },
  "disabled_nodes": [],
  "tasks": [
    {
      "proxy_group_name": "Youtube",
      "service_name": "youtube_music",
      "enabled": true
    },
    {
      "proxy_group_name": "AI",
      "service_name": "gemini",
      "enabled": true
    }
  ]
}
```

要求：

- `proxy_group_name` 必须是 Clash 中存在的 ProxyGroup 名称。
- `clash.http_proxy` 是服务探测请求使用的 HTTP 代理，通常是 Clash 的 HTTP 端口。
- Clash 需要开启 External Controller。

### 4. 运行

直接启动 TUI：

```powershell
uv run python -m clash_auto_switch
```

默认会进入 TUI，并根据 Clash 实时连接日志触发检测。例如访问 `music.youtube.com` 时，会触发 `youtube_music` 任务。

## TUI 快捷键

运行后会进入基于 Textual 的动态刷新终端界面：

- `h` / `l`: 切换服务列
- `j` / `k`: 选择节点
- `Enter`: 手动切换到选中节点
- `d`: 禁用/取消禁用选中节点
- `e`: 最大化/恢复事件窗口；最大化后可滚动查看历史事件
- `a`: 在 auto 模式中开启/关闭当前服务连接触发的自动检测与自动切换
- `c`: 手动触发当前服务检测
- `q`: 退出

节点列表字段：

- 节点: Clash 节点名；`*` 表示当前 ProxyGroup 正在使用的节点
- 得分: 历史可靠性评分
- 成功率: 历史检测成功率

右侧连接侧栏会显示当前选中服务的 Clash 活跃连接，数据来自 `GET /connections`，并按服务域名规则过滤。连接字段包括目标 Host、命中规则、代理链路、上传/下载流量和网络类型。

auto 模式会把更细的诊断事件写到数据目录下的 `diagnostics.jsonl`，启动时事件窗口会显示完整路径。该文件用于排查“触发检测、当前节点、检测结果、切换动作、连接快照”等问题。

自动触发检测会按服务使用智能频率控制：服务持续可用时，最短自动检测间隔会逐步延长；检测失败时会逐步缩短。间隔范围为 5 秒到 30 分钟。手动切换节点和 `c` 手动检测会立即执行一次检测，不受该间隔限制。`a` 开关按当前服务单独控制。

服务检测前会先通过当前节点访问 `https://cp.cloudflare.com/generate_204` 做基础连通性检查。若该检查失败，会直接跳过当前节点并尝试切换到下一个候选；该节点不会写入服务可用性历史，因此不影响得分和成功率。

按 `d` 禁用节点后，该节点会写入配置文件的 `disabled_nodes`，并从自动切换候选中跳过。禁用节点仍会显示在 TUI 列表末尾，并标记为 `[禁用]`；再次按 `d` 可取消禁用。

## 配置说明

### `clash`

```json
{
  "controller": "127.0.0.1:9097",
  "secret": null,
  "http_proxy": "http://127.0.0.1:7890"
}
```

- `controller`: Clash External Controller 地址。
- `secret`: Clash API 密钥。未设置时使用 `null`。
- `http_proxy`: 服务检测请求使用的 HTTP 代理地址。

### `monitoring`

```json
{
  "interval_sec": 300.0,
  "max_rotations": 0,
  "once": false
}
```

- `interval_sec`: 保留字段；当前默认 TUI 入口不使用周期模式。
- `max_rotations`: 保留字段；当前默认 TUI 入口不使用周期模式。
- `once`: 保留字段；当前默认 TUI 入口不使用 run-once 模式。

### `tasks`

```json
{
  "proxy_group_name": "Youtube",
  "service_name": "youtube_music",
  "enabled": true
}
```

- `proxy_group_name`: Clash ProxyGroup 名称。
- `service_name`: 服务检测名称。
- `enabled`: 是否启用。

### `disabled_nodes`

```json
[
  {
    "proxy_group_name": "Youtube",
    "service_name": "youtube_music",
    "node_name": "node-a"
  }
]
```

禁用粒度是 `(proxy_group_name, service_name, node_name)`。同一个节点可以只在某个服务下禁用。

## 支持的服务

当前默认启用检测：

- `chatgpt`
- `claude`
- `gemini`
- `youtube_premium`
- `youtube_music`
- `bahamut_anime`
- `netflix`
- `disney_plus`
- `prime_video`
- `emby_as174`

`service_name` 必须严格使用上面的服务键名，不再支持别名。

哔哩哔哩相关检测代码保留在代码中，但默认未注册启用。

## 调试命令

查看当前配置：

```powershell
uv run python -m clash_auto_switch show-config
```

查看历史统计：

```powershell
uv run python -m clash_auto_switch stats
```

查看指定服务的节点统计：

```powershell
uv run python -m clash_auto_switch stats "Youtube" "youtube_music"
```

调试指定 ProxyGroup 的切换候选排序，不执行切换：

```powershell
uv run python -m clash_auto_switch debug-switch "Youtube" "youtube_music"
```

清除节点历史统计：

```powershell
uv run python -m clash_auto_switch clear-stats
```

## Clash 配置参考

ProxyGroup 示例：

```yaml
proxy-groups:
  - name: "Youtube"
    type: select
    include-all-proxies: true

  - name: "AI"
    type: select
    proxies:
      - node-a
      - node-b
      - node-c
```

规则示例：

```yaml
rules:
  - GEOSITE,youtube,Youtube
  - DOMAIN-SUFFIX,music.youtube.com,Youtube
  - DOMAIN-SUFFIX,gemini.google.com,AI
  - MATCH,Proxy
```

更多规则格式参考 MetaCubeX 文档：

https://wiki.metacubex.one/config/rules/

## 开发文档

开发和架构说明见：

[docs/development.md](./docs/development.md)

## 说明

服务检测实现参考了 clash-verge-rev 的解锁检测思路：

https://github.com/clash-verge-rev/clash-verge-rev
