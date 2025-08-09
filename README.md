### 项目简介

`clash_auto_switch` 是自动切换Clash节点，以保证目标服务（如 ChatGPT、Netflix、Disney+ 等）可用的小工具。

程序会按指定间隔检测服务，未解锁/不可用时自动切到下一个可用节点。

### 先决条件

- 已安装并运行 Clash/Clash.Meta，且开启 External Controller（REST API）。
  - 例如在 Clash 配置中：
    - `external-controller: 127.0.0.1:9097`
    - 若设置了 `secret`，运行本工具时需通过 `--secret` 传入。
- Python 3.9+ 环境。

### 安装

```bash
pip install .
```

### 运行

示例：一次性检测 ChatGPT，成功即退出
```bash
clash_auto_switch "YourGroup" "chatgpt"
```

示例：持续监控 Netflix，失败则切换节点
```bash
clash_auto_switch "YourGroup" "netflix" --monitor
```

示例：查看节点可靠性统计
```bash
clash_auto_switch "YourGroup" "netflix" --show-stats
```

按 Ctrl-C 可以随时退出。

### 参数说明

- 必选参数：
  - `proxy_group_name`：Clash 中要轮换的代理组名称
  - `service_name`：要检测的服务名
- 可选参数：
  - `--controller`（默认 `127.0.0.1:9097`）：Clash External Controller 地址。
  - `--secret`：若 Clash 开启了 API 密钥，需传入此 Secret。
  - `--http-proxy`（默认 `http://127.0.0.1:7890`）：探测请求所走的 HTTP 代理（通常指向 Clash 的本地 HTTP 端口）。
  - `--interval`（默认 `30.0` 秒）：检测/切换的间隔。
  - `--max-rotations`（默认 `0`）：最大连续切换次数；`0` 表示不限制。达到上限后会短暂等待并继续监控。
  - `--monitor`（默认关闭）：开启后持续后台监控；关闭时，一旦检测到可用即退出。
  - `--show-stats`：显示节点可靠性统计信息并退出。

### 支持的服务与别名

以下名称大小写不敏感：

- ChatGPT：`chatgpt`, `openai`
- Netflix：`netflix`
- Disney+：`disney+`, `disney`, `disney_plus`
- Prime Video：`prime_video`, `prime`, `amazon_prime`
- Gemini：`gemini`
- YouTube Premium：`youtube_premium`, `youtube`
- 哔哩哔哩大陆：`bilibili_mainland`, `bilibili_cn`
- 哔哩哔哩港澳台：`bilibili_hk_mc_tw`, `bilibili_hk`
- 动画疯：`bahamut_anime`, `bahamut`


### 常见问题

- 提示无法连接或 401：
  - 确认 `--controller` 地址正确且 Clash 已运行；
  - 若 Clash 设置了 `secret`，必须通过 `--secret` 传入；
  - 某些实现需要 `http://` 协议前缀，尝试 `--controller http://127.0.0.1:9097`。


### 其他

服务检测代码基于[clash-verge-rev](https://github.com/clash-verge-rev/clash-verge-rev). 


### 节点可靠性评估

程序会为每个"节点-服务"组合自动计算可靠性评分（0.0-1.0），帮助您了解哪些节点最适合特定服务：

通过 `--show-stats` 可查看按可靠性排序的节点列表

#### 统计信息示例
```
=== 统计信息: MyProxyGroup / netflix ===
总节点数: 3
总检测次数: 45
整体成功率: 73.33%
最可靠节点: US-Node-A (可靠性评分: 0.856)

📊 节点可靠性排名:
   1. US-Node-A         可靠性: 0.856 成功率: 85% 检测次数:  20 ✅
   2. US-Node-B         可靠性: 0.734 成功率: 70% 检测次数:  15 ❌
   3. US-Node-C         可靠性: 0.412 成功率: 60% 检测次数:  10 ❌
```