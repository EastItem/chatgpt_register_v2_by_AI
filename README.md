# ChatGPT / Codex 自动注册工具 v2.0

Codex 自动注册与 OAuth Token 生成工具集，支持 **Skymail 自建邮箱**和**自定义 IMAP 邮箱**（推荐 2925 无限别名服务），并提供 **CPA 凭证自动检测与替换**功能。

## ✨ v2.0 重大更新

项目已完成模块化重构，并持续迭代新功能：

### 主要改进

- ✅ **完全模块化**：代码拆分为独立模块，易于维护和扩展
- ✅ **100% 成功率**：优化 OAuth 流程，实现稳定的 100% 成功率
- ✅ **智能重试机制**：自动处理 TLS 错误和 Cookie 未设置问题（最多 3 次重试）
- ✅ **性能优化**：平均注册时间从 60 秒降至 28.6 秒（提升 52%）
- ✅ **高并发支持**：支持 5 线程并发，保持 100% 成功率
- ✅ **独立运行**：v2 版本完全独立，不依赖原始代码
- ✅ **自定义 IMAP 邮箱**：支持任意 IMAP 邮箱，推荐使用 2925 无限别名服务
- ✅ **CPA 自动检测替换**：一键检测 CPA 中的 401 失效凭证，并支持自动删除

## 📦 项目结构

```
.
├── lib/                          # 核心库模块
│   ├── config.py                 # 配置加载
│   ├── skymail_client.py         # Skymail 邮箱客户端
│   ├── imap_client.py            # 自定义 IMAP 邮箱客户端（支持 2925 别名）
│   ├── chatgpt_client.py         # ChatGPT 注册客户端
│   ├── oauth_client.py           # OAuth 登录客户端
│   ├── sentinel_token.py         # Sentinel Token 生成器
│   ├── token_manager.py          # Token 管理器
│   └── utils.py                  # 工具函数
├── account_monitor/              # 账号自动监控与替换模块
├── chatgpt_register_v2.py        # v2.0 注册工具（推荐）
├── cpa_utils.py                  # CPA 凭证检测 / 上传工具
├── config.json                   # 配置文件
└── README.md                     # 本文档
```

## 功能特性

- 🚀 **双邮箱模式**：支持 Skymail 自建邮箱和自定义 IMAP 邮箱（推荐 2925）
- 📨 **2925 无限别名**：使用 `prefix+random@2925.com` 格式，无需额外建站
- 🌐 支持多个域名后缀：在 `config.json` 里面配置
- 🤖 自动注册 ChatGPT 账号并获取验证码
- 🔑 自动生成 OAuth Token（Access Token / Refresh Token）
- ⚡ 支持高并发注册（推荐 2-5 线程）
- 🔄 智能重试机制（TLS 错误、Cookie 未设置自动重试）
- 💾 自动保存账号信息和 Token 到文件
- 📊 实时显示注册进度和成功率
- 🔍 **CPA 凭证自动检测**：批量检测 CPA 中 401 失效账号，支持自动删除和上传

## 环境要求

- Python 3.7+
- 邮箱服务（二选一）：
  - **Skymail** 自建邮箱服务（需要管理员账号）
  - **IMAP 邮箱**，推荐 [2925](https://www.2925.com) 无限别名邮箱（无需建站）
- 代理（可选，用于访问 OpenAI 服务）

## 安装依赖

```bash
# 核心依赖（注册工具）
pip install curl_cffi

# 可选依赖（CPA 检测工具 / account_monitor）
pip install aiohttp requests
```

## 配置说明

复制 `config.example.json` 为 `config.json` 并修改配置：

```json
{
    "skymail_admin_email": "admin@example.com",
    "skymail_admin_password": "your_password_here",
    "skymail_domains": [],

    "use_imap": false,
    "imap_server": "imap.2925.com",
    "imap_port": 993,
    "imap_user": "your_2925_account@2925.com",
    "imap_password": "your_imap_password",
    "email_prefix": "myprefix",
    "email_domain": "2925.com",

    "proxy": "http://127.0.0.1:7890",
    "output_file": "registered_accounts.txt",
    "enable_oauth": true,
    "oauth_required": true,
    "oauth_issuer": "https://auth.openai.com",
    "oauth_client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
    "oauth_redirect_uri": "http://localhost:1455/auth/callback",
    "ak_file": "ak.txt",
    "rk_file": "rk.txt",
    "token_json_dir": "tokens"
}
```

### 重要配置项说明

1. **Skymail 模式**（`use_imap: false`，默认）：
   - `skymail_admin_email` / `skymail_admin_password`：Skymail 管理员账号
   - API 地址自动从邮箱域名提取，程序启动时自动生成 API Token

2. **IMAP 模式**（`use_imap: true`，推荐 2925）：
   - `imap_server`：IMAP 服务器地址，2925 填 `imap.2925.com`
   - `imap_port`：IMAP SSL 端口，默认 `993`
   - `imap_user` / `imap_password`：IMAP 登录账号和密码
   - `email_prefix`：别名前缀（如 `myprefix`），注册邮件将发送到 `myprefix+<随机生成后缀>@2925.com`
   - `email_domain`：邮件域名，2925 填 `2925.com`

3. **proxy**：
   - 代理地址（可选）
   - 格式：`http://host:port` 或 `socks5://host:port`

4. **enable_oauth** 和 **oauth_required**：
   - `enable_oauth`: 是否启用 OAuth 登录
   - `oauth_required`: OAuth 失败时是否视为注册失败

## 使用方法

### 注册工具

```bash
# 注册 1 个账号（默认）
python chatgpt_register_v2.py

# 注册 5 个账号，使用 3 个线程
python chatgpt_register_v2.py -n 5 -w 3

# 注册 10 个账号，使用 5 个线程，不启用 OAuth
python chatgpt_register_v2.py -n 10 -w 5 --no-oauth
```

#### 命令行参数

- `-n, --num`: 注册账号数量（默认: 1）
- `-w, --workers`: 并发线程数（默认: 1）
- `--no-oauth`: 禁用 OAuth 登录

#### 推荐配置

| 场景 | 线程数 | 说明 |
|------|--------|------|
| 稳定优先 | 1-2 | 100% 成功率，速度较慢 |
| 平衡模式 | 3 | 100% 成功率，速度适中 |
| 速度优先 | 4-5 | 100% 成功率，速度最快 |

### 输出文件

- `registered_accounts.txt`：账号密码列表
- `ak.txt`：Access Token 列表
- `rk.txt`：Refresh Token 列表
- `tokens/`：每个账号的完整 Token JSON 文件

## CPA 凭证自动检测与替换

`cpa_utils.py` 提供 CPA（CliProxyAPI）凭证的批量检测、自动删除和上传功能。

### 快速使用

```bash
# 1. 仅检测 401 失效凭证（不删除）
python cpa_utils.py --cpa-token Bearer_xxx

# 2. 检测并自动删除 401 凭证
python cpa_utils.py --cpa-token Bearer_xxx --delete

# 3. 指定 CPA 地址
python cpa_utils.py --cpa-base-url http://localhost:8317 --cpa-token Bearer_xxx --delete

# 4. 保存检测结果到 JSON 文件
python cpa_utils.py --cpa-token Bearer_xxx --output result.json

# 5. 批量上传 tokens/ 目录下的 JSON 文件到 CPA
python cpa_utils.py --cpa-token Bearer_xxx --upload-dir ./tokens
```

### 主要参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--cpa-base-url` | CPA 服务地址 | `http://localhost:8317` |
| `--cpa-token` | CPA 管理 Bearer token（必填） | — |
| `--workers` | 并发检测数 | `6` |
| `--timeout` | 请求超时（秒） | `20` |
| `--retries` | 失败重试次数 | `1` |
| `--delete` | 自动删除检测到的 401 凭证 | 关闭 |
| `--upload-dir` | 批量上传 JSON 文件的目录 | — |
| `--output` | 检测结果输出路径（JSON） | — |
| `--batch-delay` | 批次间延迟（秒） | `2.0` |
| `--verbose` | 显示详细错误信息 | 关闭 |

### 自动监控与替换（account_monitor）

如需持续监控 CPA 中的账号状态并**自动注册新账号替换失效账号**，请参阅 [account_monitor/README.md](account_monitor/README.md)。

## 工作原理

### 1. 邮箱创建

**Skymail 模式**：
- 随机选择域名，生成随机前缀（6-10 位字母数字组合）

**IMAP 模式（推荐 2925）**：
- 使用固定前缀 + 随机生成后缀组合别名：`prefix+<随机生成后缀>@2925.com`
- 无需建站，利用 2925 无限别名特性接收验证码

### 2. 账号注册
- 访问 ChatGPT 注册页面
- 提交邮箱和密码
- 自动获取邮箱验证码（优化轮询：前 10 秒每 0.5 秒，之后每 2 秒）
- 完成账号创建

### 3. OAuth 登录（v2.0 优化）
- Bootstrap OAuth session（确保获取 login_session cookie）
- 提交邮箱和密码
- 处理 OTP 验证（自动去重验证码）
- Workspace/Organization 选择
- 获取 Authorization Code
- 换取 Access Token 和 Refresh Token

### 4. 智能重试机制
- **TLS 错误重试**：自动重试最多 3 次
- **Cookie 未设置重试**：重新访问 consent URL，最多 3 次
- **整个流程重试**：OAuth 失败时重新注册，最多 3 次

## 性能数据

基于实际测试（5 个账号，5 线程并发）：

- **成功率**：100% (5/5)
- **平均耗时**：28.6 秒/账号
- **总耗时**：143 秒（包含重试）
- **重试次数**：2 次（自动成功）

## 注意事项

### 1. 邮箱服务

**Skymail 模式**：
- 需要自己搭建 Skymail 邮箱服务
- 确保 Skymail 服务可正常访问，管理员账号需要有足够权限

**IMAP 模式（推荐 2925）**：
- 注册 [2925.com](https://www.2925.com) 账号，开启 IMAP 访问权限
- 在 `config.json` 中设置 `"use_imap": true` 并填入 IMAP 账号信息
- 2925 别名格式：`你的账号+<随机生成后缀>@2925.com`，工具自动生成后缀，验证邮件会自动路由到主邮箱

### 2. 代理设置
- 如果在国内使用，建议配置代理
- 确保代理可以访问 OpenAI 服务

### 3. 并发控制
- **推荐并发数**：2-5 线程
- **不推荐**：超过 5 线程（可能导致 TLS 连接池耗尽）
- 首次使用建议从 1 线程开始测试

### 4. Token 有效期
- Access Token 有效期较短
- Refresh Token 可用于刷新 Access Token
- 建议定期备份 Token 文件

### 5. CPA 检测工具
- 运行前需安装 `aiohttp`：`pip install aiohttp`
- 建议先不加 `--delete` 参数预览结果，确认无误再执行删除
