# DeepCode Agent 中文说明

DeepCode 是一个面向软件工程场景的 AI 智能体系统，支持多智能体协作、工具调用、平台消息桥接和可视化运维。

对应英文文档请见: [README.md](README.md)

## 核心特性

- 多智能体协作: 编排、编码、评审、测试等角色化执行
- 工程闭环: 计划 -> 编码 -> 执行 -> 评审 -> 测试
- 多入口: CLI、REST API、Reflex Web UI、平台桥接
- 平台桥接: 统一接入 generic、QQ、微信、飞书
- QQ 双通道: 官方 Bot API/Gateway 或 NapCat/OneBot
- 持久化与治理: SQLite、运行时覆盖、审计与策略控制

## 快速开始

### 环境要求

- Python 3.11+
- Node.js (Reflex 前端构建需要)
- OpenAI 兼容 API Key，或 Ollama，或 mock provider

### 开发安装

```bash
git clone https://github.com/lifeislikeaboat567/DeepCode.git
cd DeepCode
pip install -e ".[dev]"
copy .env.example .env
```

至少配置以下变量:

```bash
DEEPCODE_LLM_PROVIDER=openai
DEEPCODE_LLM_MODEL=gpt-4o-mini
DEEPCODE_LLM_API_KEY=sk-...
```

安装验证:

```bash
deepcode --version
deepcode doctor
```

## 常用 CLI

```bash
# 如果 deepcode 不在 PATH，可使用模块入口
python -m deepcode doctor

# 交互对话
deepcode chat

# 执行多智能体任务
deepcode run "Build a Python binary search tree with tests"

# 启动 API
deepcode serve

# 启动 Reflex Web UI
deepcode ui
```

## QQ 官方 Bot 接入 (AppID + AppSecret)

本次更新后，官方 QQ Bot 支持仅使用 AppID + AppSecret 完成鉴权与通信。

### 1. 配置环境变量

```bash
DEEPCODE_CHAT_BRIDGE_ENABLED=true
DEEPCODE_CHAT_BRIDGE_ALLOWED_PLATFORMS=qq
DEEPCODE_CHAT_BRIDGE_DEFAULT_MODE=agent
DEEPCODE_CHAT_BRIDGE_CALLBACK_DELIVERY_ENABLED=true

# 官方 QQ Bot
DEEPCODE_CHAT_BRIDGE_QQ_DELIVERY_MODE=official
DEEPCODE_CHAT_BRIDGE_QQ_BOT_APP_ID=<your_app_id>
DEEPCODE_CHAT_BRIDGE_QQ_BOT_APP_SECRET=<your_app_secret>

# 可选: 指定 QQ Ed25519 签名密钥
DEEPCODE_CHAT_BRIDGE_QQ_SIGNING_SECRET=
```

说明:

- DeepCode 会自动用 AppID + AppSecret 换取 access_token。
- 若未设置 `DEEPCODE_CHAT_BRIDGE_QQ_SIGNING_SECRET`，会回退使用 AppSecret 做签名校验。

### 2. 打开官方 Gateway 监听

```bash
deepcode qqgateway start
deepcode qqgateway status
deepcode qqgateway stop
```

模块入口:

```bash
python -m deepcode qqgateway start
python -m deepcode qqgateway status
python -m deepcode qqgateway stop
```

前台调试运行:

```bash
deepcode qqgateway run --skip-preflight
```

### 3. 验证状态

```bash
deepcode qqgateway status
deepcode qqgateway status --json-output
```

重点检查:

- `running=true`
- `credentials_ready=true`
- 日志文件路径可见 (例如 `~/.deepcode/qq_gateway_listener.log`)

### 4. 常见问题

- `missing_credentials`: 检查 `DEEPCODE_CHAT_BRIDGE_QQ_BOT_APP_ID` 与 `DEEPCODE_CHAT_BRIDGE_QQ_BOT_APP_SECRET`
- gateway 重连频繁: 检查 QQ 开放平台配置、网络出口和系统时间
- 已收消息但无回包: 检查 `qq_gateway_listener.log` 中 `bridge_event_type` 与 `QQ gateway reply sent`

## NapCat 接入 (可选)

若使用 NapCat/OneBot，请改为:

```bash
DEEPCODE_CHAT_BRIDGE_QQ_DELIVERY_MODE=napcat
DEEPCODE_CHAT_BRIDGE_QQ_NAPCAT_API_BASE_URL=http://127.0.0.1:3000
DEEPCODE_CHAT_BRIDGE_QQ_NAPCAT_ACCESS_TOKEN=
DEEPCODE_CHAT_BRIDGE_QQ_NAPCAT_WEBHOOK_TOKEN=
```

监听方式:

```bash
deepcode napcat -p 18000
# 或
deepcode inbound start --port 18000
deepcode inbound status
deepcode inbound stop
```

## API 与文档

启动服务后可访问:

- OpenAPI 文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/api/v1/health

## 说明

- Web UI 主路径为 Reflex，`deepcode/ui` 仅保留兼容壳层。
- 平台桥接请求入口: `/api/v1/platforms/{platform}/events`
