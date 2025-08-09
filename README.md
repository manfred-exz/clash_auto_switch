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

本工具完全基于配置文件模式，可以同时监控多个代理组和服务：

#### 基本使用流程

1. 生成配置文件模板：
```bash
clash_auto_switch --generate-config
```

2. 查看配置文件位置和内容：
```bash
clash_auto_switch --show-config
```

3. 根据需要编辑配置文件（见下方配置说明）

4. 运行监控：
```bash
# 使用配置文件中的设置运行（默认持续监控）
clash_auto_switch

# 只运行一次，服务可用后退出
clash_auto_switch --once
```

#### 查看统计信息

查看特定代理组和服务的节点可靠性统计：
```bash
clash_auto_switch --show-stats "YourGroup" "netflix"
```

按 Ctrl-C 可以随时退出。

### 配置文件说明

配置文件自动保存在与节点历史数据相同的位置（跨平台标准数据目录），采用 JSON 格式，包含以下部分：

```json
{
  "clash": {
    "controller": "127.0.0.1:9097",
    "secret": null,
    "http_proxy": "http://127.0.0.1:7890"
  },
  "monitoring": {
    "interval_sec": 30.0,
    "max_rotations": 0,
    "once": false
  },
  "tasks": [
    {
      "name": "ChatGPT-US",
      "proxy_group_name": "🇺🇸美国",
      "service_name": "chatgpt",
      "enabled": true
    },
    {
      "name": "Netflix-HK", 
      "proxy_group_name": "🇭🇰香港",
      "service_name": "netflix",
      "enabled": true
    }
  ]
}
```

配置选项说明：
- `clash.controller`：Clash External Controller 地址
- `clash.secret`：Clash API 密钥（如未设置则为 null）
- `clash.http_proxy`：探测请求所走的 HTTP 代理地址
- `monitoring.interval_sec`：检测间隔（秒）
- `monitoring.max_rotations`：最大连续切换次数（0 表示无限制）
- `monitoring.once`：是否只运行一次（false 表示持续监控，true 表示服务可用后退出）
- `tasks`：监控任务列表
  - `name`：任务名称（用于日志区分）
  - `proxy_group_name`：Clash 代理组名称
  - `service_name`：服务名称
  - `enabled`：是否启用该任务

### 命令行参数

- `--generate-config`：生成配置文件模板到默认位置
- `--show-config`：显示当前配置文件位置和内容  
- `--once`：只运行一次，服务可用后退出（覆盖配置文件设置）
- `--show-stats PROXY_GROUP SERVICE`：显示指定代理组和服务的节点统计信息并退出

**默认行为**：程序默认进入持续监控模式，会一直运行直到手动停止。

所有其他配置（Clash地址、代理端口、检测间隔等）都通过配置文件管理。

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