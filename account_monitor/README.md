# account_monitor - 账号自动监控与替换

定时检测 CPA（CliProxyAPI）中的 Codex 账号状态，发现封号或额度不足时，自动注册新账号并替换。异常账号不会直接删除，而是保存到隔离文件夹中，支持后续批量复查。

## 功能

- 🔍 定时从 CPA 拉取所有 codex 账号凭证
- ❌ 检测封号账号（接口返回 HTTP 401）
- 📉 检测额度不足账号（余额低于配置阈值）
- 🗂️ 异常账号保存到隔离文件夹（不直接删除）：
  - `quarantine/banned/` — 封号账号
  - `quarantine/quota_low/` — 额度不足账号
- ⚙️ 自动注册新账号并上传到 CPA，同时从 CPA 删除旧账号
- 🔁 支持守护模式（定时循环）和单次运行模式
- 🔎 支持对隔离账号批量复查，返回统计信息和详细状态

## 目录结构

```
account_monitor/
├── __init__.py
├── __main__.py            # python -m account_monitor 入口
├── monitor.py             # 主监控逻辑 + CLI
├── quota_checker.py       # CPA 接口检测封号和额度
├── account_replacer.py    # 注册新账号并替换
├── quarantine_manager.py  # 隔离账号保存与复查
├── config.example.json    # 配置模板
└── README.md
```

## 快速开始

### 1. 准备配置文件

复制配置模板：

```bash
cp account_monitor/config.example.json account_monitor/config.json
```

编辑 `account_monitor/config.json`：

```json
{
    "cpa_base_url": "http://localhost:8317",
    "cpa_token": "your_cpa_bearer_token",
    "check_interval_seconds": 3600,
    "quota_threshold": 5.0,
    "auto_replace": true,
    "quarantine_dir": "",
    "dry_run": false
}
```

同时确保根目录的 `config.json` 已正确配置注册所需的邮箱和代理信息。

### 2. 安装依赖

```bash
pip install requests aiohttp curl_cffi
```

### 3. 运行

**单次检测（不替换）：**

```bash
python -m account_monitor --once --no-replace
```

**单次检测并自动替换异常账号：**

```bash
python -m account_monitor --once
```

**守护模式（每小时检测一次）：**

```bash
python -m account_monitor
```

**自定义间隔（每 30 分钟）：**

```bash
python -m account_monitor --interval 1800
```

**指定 CPA 地址和 token：**

```bash
python -m account_monitor --cpa-url http://localhost:8317 --cpa-token Bearer_xxx --once
```

**模拟运行（不实际注册或删除）：**

```bash
python -m account_monitor --dry-run --once
```

**复查隔离文件夹中的账号状态：**

```bash
python -m account_monitor --check-quarantine --cpa-token Bearer_xxx
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--config` | 监控配置文件路径 | `account_monitor/config.json` |
| `--cpa-url` | CPA 服务地址 | `http://localhost:8317` |
| `--cpa-token` | CPA 管理 token | （必填）|
| `--interval` | 守护模式检测间隔（秒） | `3600` |
| `--quota-threshold` | 额度阈值，低于此值触发替换 | 不检测额度 |
| `--once` | 仅执行一次后退出 | 守护模式 |
| `--no-replace` | 只检测，不自动替换 | 自动替换 |
| `--check-quarantine` | 复查隔离账号状态 | — |
| `--quarantine-dir` | 隔离文件夹根目录 | `account_monitor/quarantine/` |
| `--dry-run` | 模拟运行，不实际操作 | 关闭 |
| `--log-level` | 日志级别 | `INFO` |
| `--log-file` | 日志文件路径 | 控制台输出 |

## 配置文件说明

| 参数 | 类型 | 说明 |
|------|------|------|
| `cpa_base_url` | string | CPA 服务地址 |
| `cpa_token` | string | CPA 管理 Bearer token |
| `check_interval_seconds` | int | 定时检测间隔（秒），默认 3600 |
| `quota_threshold` | float/null | 额度阈值，`null` 表示不检测额度 |
| `target_type` | string | 检测的账号类型，默认 `"codex"` |
| `request_timeout` | int | HTTP 请求超时（秒），默认 20 |
| `auto_replace` | bool | 是否自动替换异常账号，默认 `true` |
| `max_replacements_per_run` | int/null | 单次最多替换数量，`null` 表示全部替换 |
| `quarantine_dir` | string | 隔离文件夹根目录，空字符串使用默认路径 `account_monitor/quarantine/` |
| `dry_run` | bool | 模拟模式，不实际注册或删除，默认 `false` |
| `log_level` | string | 日志级别，默认 `"INFO"` |
| `log_file` | string | 日志文件路径，空表示只输出到控制台 |

## 环境变量支持

所有配置均可通过环境变量覆盖，适合 Docker/CI 部署：

```bash
CPA_BASE_URL=http://localhost:8317 \
CPA_TOKEN=Bearer_xxx \
MONITOR_INTERVAL=1800 \
QUOTA_THRESHOLD=5.0 \
QUARANTINE_DIR=/data/quarantine \
python -m account_monitor
```

| 环境变量 | 对应配置 |
|----------|----------|
| `CPA_BASE_URL` | `cpa_base_url` |
| `CPA_TOKEN` | `cpa_token` |
| `MONITOR_INTERVAL` | `check_interval_seconds` |
| `QUOTA_THRESHOLD` | `quota_threshold` |
| `TARGET_TYPE` | `target_type` |
| `REQUEST_TIMEOUT` | `request_timeout` |
| `AUTO_REPLACE` | `auto_replace` |
| `MAX_REPLACEMENTS` | `max_replacements_per_run` |
| `QUARANTINE_DIR` | `quarantine_dir` |
| `DRY_RUN` | `dry_run` |
| `LOG_LEVEL` | `log_level` |
| `LOG_FILE` | `log_file` |

## 隔离机制

发现异常账号时，不会直接删除，而是先将其数据保存到本地隔离文件夹：

```
account_monitor/quarantine/
├── banned/
│   └── account@example.com.json   # 封号账号
└── quota_low/
    └── another@example.com.json   # 额度不足账号
```

每个隔离文件的格式：

```json
{
    "quarantine_reason": "banned",
    "quarantine_time": "2025-01-01T12:00:00+00:00",
    "cpa_name": "account@example.com.json",
    "quota_remaining": null,
    "token_data": {
        "type": "codex",
        "email": "account@example.com",
        "access_token": "...",
        "refresh_token": "..."
    }
}
```

> `token_data` 来自本地 `tokens/` 目录中对应的 JSON 文件。若注册时未生成本地文件，此字段为 `null`（该账号无法进行在线复查）。

## 隔离账号复查

使用 `--check-quarantine` 对隔离文件夹中的所有账号重新检测，输出统计和明细：

```bash
python -m account_monitor --check-quarantine --cpa-token Bearer_xxx
```

输出示例：

```json
{
  "quarantine_dir": "/path/to/account_monitor/quarantine",
  "stats": {
    "total": 5,
    "rechecked": 4,
    "still_banned": 2,
    "still_quota_low": 0,
    "recovered": 2,
    "check_error": 0,
    "no_token_data": 1
  },
  "details": [
    {
      "file": "...",
      "cpa_name": "account1@example.com.json",
      "quarantine_reason": "banned",
      "quarantine_time": "2025-01-01T12:00:00+00:00",
      "current_status": "still_banned",
      "quota_remaining": null,
      "error": null
    }
  ]
}
```

`current_status` 取值：

| 值 | 说明 |
|----|------|
| `still_banned` | 仍封号（HTTP 401）|
| `still_quota_low` | 仍额度不足 |
| `recovered` | 已恢复正常（可考虑重新上传到 CPA）|
| `check_error` | 检测失败（网络错误等）|
| `no_token_data` | 隔离文件中无凭证数据，无法检测 |

## 检测逻辑

1. 通过 `/v0/management/auth-files` 获取 CPA 中所有凭证
2. 筛选 `type == "codex"` 的账号
3. 对每个账号，通过 `/v0/management/api-call` 代理调用 `wham/usage` 接口：
   - 返回 HTTP **401** → 判定为**封号**
   - 返回 HTTP **200** 且余额低于 `quota_threshold` → 判定为**额度不足**
4. 对异常账号：
   - 注册新账号（调用主项目的注册流程）
   - 上传新账号 Token JSON 到 CPA
   - 将旧账号数据保存到本地隔离文件夹
   - 从 CPA 删除旧账号凭证

## 注意事项

- 运行前请确保根目录的 `config.json` 已正确配置（邮箱服务、代理等）
- 建议先用 `--dry-run --once` 测试检测逻辑，确认无误后再开启自动替换
- 守护模式下按 `Ctrl+C` 可优雅退出
- 日志默认输出到控制台，可通过 `log_file` 配置持久化日志
- 隔离文件夹中的已恢复账号需手动处理（如重新上传到 CPA）
