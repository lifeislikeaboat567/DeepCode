# OpenClaw 与 Claude Code 功能调研（V2）

> 日期：2026-03-17  
> 目标：为 DeepCode CLI/WebUI 功能扩展提供可执行基线  
> 范围：OpenClaw + Claude Code 官方文档能力域全覆盖（按功能族拆解）

---

## 一、调研口径

### 1.1 口径说明

- 本文采用“能力域全覆盖 + 关键子能力列举”的方式，而非仅列举零散功能点。
- 由于上游产品持续发布，本文默认“能力域稳定、子能力可迭代补充”。
- 结论优先聚焦 DeepCode 可落地能力，不做纯营销对比。

### 1.2 主要来源（官方）

- Claude Code Overview: https://code.claude.com/docs/en/overview
- Claude Code Quickstart: https://code.claude.com/docs/en/quickstart
- Claude Code CLI Reference: https://code.claude.com/docs/en/cli-reference
- Claude Code Settings: https://code.claude.com/docs/en/settings
- Claude Code Hooks: https://code.claude.com/docs/en/hooks
- Claude Code MCP: https://code.claude.com/docs/en/mcp
- Claude Code Common Workflows: https://code.claude.com/docs/en/common-workflows
- Claude Code Memory: https://code.claude.com/docs/en/memory
- OpenClaw 官网: https://openclaw.ai
- OpenClaw 文档（Getting Started）: https://docs.openclaw.ai/start/getting-started
- OpenClaw 文档（Session）: https://docs.openclaw.ai/concepts/session
- OpenClaw 文档（Browser）: https://docs.openclaw.ai/tools/browser
- OpenClaw 文档（Skills）: https://docs.openclaw.ai/tools/skills
- OpenClaw 文档（Exec Tool）: https://docs.openclaw.ai/tools/exec
- OpenClaw Integrations: https://openclaw.ai/integrations
- OpenClaw GitHub: https://github.com/openclaw/openclaw

---

## 二、Claude Code 功能全景（能力域）

### 2.1 多界面运行与会话模式

- 终端 CLI（主形态）
- IDE 集成（VS Code / JetBrains）
- 桌面与 Web
- 远程控制与跨设备续接
- 交互会话、一次性执行、无交互 Headless

### 2.2 CLI 能力族

- 会话管理：continue、resume、命名、fork、from-pr
- 任务执行：交互模式 + print 模式
- 输出格式：text / json / stream-json
- 预算与轮次控制：max-budget、max-turns
- 模型控制：model、fallback-model、effort
- 工具权限：tools、allowedTools、disallowedTools
- 系统提示拼接与替换：append-system-prompt、system-prompt
- 工作区扩展：add-dir、worktree
- 远程桥接：remote、remote-control、teleport

### 2.3 权限、安全与沙箱

- 权限模式：default / plan / acceptEdits / dontAsk / bypassPermissions
- 细粒度规则：allow / ask / deny（支持工具级模式）
- 沙箱：文件系统写入白黑名单、网络域名控制、本地绑定、socket 控制
- 托管策略：企业级 managed settings，强制策略不可被用户覆盖

### 2.4 Hook 自动化体系

- 生命周期钩子覆盖完整会话闭环：
  SessionStart、UserPromptSubmit、PreToolUse、PermissionRequest、PostToolUse、PostToolUseFailure、Notification、SubagentStart、SubagentStop、Stop、TeammateIdle、TaskCompleted、ConfigChange、WorktreeCreate、WorktreeRemove、PreCompact、PostCompact、Elicitation、ElicitationResult、SessionEnd
- Hook 处理器类型：command / http / prompt / agent
- 支持异步后台 Hook 与调试模式
- 可按作用域配置（User/Project/Local/Managed/Plugin）

### 2.5 MCP 生态能力

- 服务器接入：stdio / http / sse（SSE 已标记过时）
- 生命周期：add / list / get / remove / authenticate
- 多作用域与优先级：local / project / user / managed
- OAuth 认证、回调端口、预配置凭据
- MCP 工具搜索（按需加载），降低上下文占用
- MCP resources 与 MCP prompts 命令化调用
- Claude Code 可反向作为 MCP Server（mcp serve）

### 2.6 记忆、规则与上下文装载

- CLAUDE.md 多层级加载
- .claude/rules 路径规则化注入
- Auto Memory（按项目持久化，入口 MEMORY.md）
- 内存治理：启用/禁用、目录迁移、审计编辑

### 2.7 Agent 与协作

- 子 Agent（内置 + 自定义）
- Plan Mode（只读分析 + 计划）
- Agent Teams 与 teammate 生命周期控制
- Worktree 隔离并行执行

---

## 三、OpenClaw 功能全景（能力域）

### 3.1 系统形态与运行架构

- Gateway 为核心控制平面（会话状态与路由源）
- Dashboard / Control UI（浏览器管理台）
- 聊天渠道驱动（IM 作为统一入口）
- 本地部署优先，可扩展远端节点

### 3.2 多渠道接入与交互

- WhatsApp / Telegram / Discord / Slack / Signal / iMessage / Teams / Matrix 等
- DM 与群聊隔离策略（dmScope、per-channel-peer 等）
- 会话键映射与跨通道身份合并（identityLinks）

### 3.3 Session 与运维治理

- 会话存储与 transcript 管理
- 会话重置策略：daily / idle / by-type / by-channel
- 维护策略：prune、maxEntries、rotate、磁盘预算
- send policy（按会话类型和渠道阻断发送）

### 3.4 Skills 与插件生态

- Skill 目录分层：bundled / ~/.openclaw/skills / workspace/skills
- 技能元数据门控：二进制、环境变量、配置依赖
- ClawHub 安装、更新、同步
- 插件可携带 Skills 并参与优先级合并

### 3.5 Browser 自动化体系

- 受控独立浏览器 profile（openclaw）
- user profile 复用真实登录态（Chrome DevTools MCP attach）
- 标签页、快照、动作、截图、PDF、下载、状态控制
- 本地/远端 CDP、node browser proxy、多 profile 切换
- SSRF 策略、loopback 访问、安全令牌保护

### 3.6 Exec 与主机执行安全

- exec 工具可在 sandbox / gateway / node 执行
- approvals + allowlist + safe bins 模型
- 运行态参数：timeout、background、pty、security、ask、elevated
- 会话级 /exec 覆盖策略

### 3.7 集成广度

- 模型层：Anthropic/OpenAI/Gemini/MiniMax/Grok/OpenRouter 等
- 生产力：GitHub/Notion/Obsidian/Trello 等
- 自动化：Cron/Webhooks/Gmail Trigger
- 设备和平台：macOS/iOS/Android/Windows/Linux

---

## 四、竞品能力矩阵（对 DeepCode 最关键）

| 能力域 | Claude Code | OpenClaw | 对 DeepCode 的启示 |
|---|---|---|---|
| CLI 深度 | 强（参数与模式完整） | 中（运维命令丰富） | 需要从“聊天 CLI”升级为“工程工作台 CLI” |
| Web 控制台 | 中（更多在 IDE/CLI） | 强（Dashboard + 控制平面） | 需要任务中心、会话中心、系统状态中心 |
| 会话治理 | 强（resume/fork/pr） | 强（dmScope/reset/maintenance） | 需要多会话策略、清理与归档 |
| 扩展生态 | 强（MCP + plugin） | 强（skills + integrations） | 需要 MCP/Plugin/Skill 三层扩展策略 |
| 自动化策略 | 强（Hook 生命周期） | 强（Cron/Webhook + approvals） | 需要事件驱动自动化与策略门控 |
| 安全治理 | 强（managed policy） | 强（审批与隔离） | 需要组织级策略与审计 |

---

## 五、DeepCode 当前差距（基于仓库现状）

### 5.1 已具备

- 基础 CLI：chat / run / serve / ui
- 基础 WebUI：聊天页、运行任务页、about 页
- API：health/chat/sessions/tasks（含 SSE）
- 多 Agent 基础编排雏形（orchestrator）
- 基础工具：code_executor / file_manager / shell

### 5.2 关键缺口

- CLI 缺少工程级工作流命令族（会话管理、计划模式、审查模式、配置模式）
- WebUI 缺少任务中心、会话治理、工件管理、实时观测与权限面板
- 扩展层缺少 MCP / Plugin / Skill 机制
- 自动化层缺少 Hook 事件总线与策略引擎
- 安全层缺少企业级策略分层与审计流水
- 协作层缺少并行 worktree、多 agent 团队模式

### 5.3 基线问题（立即处理）

- 当前终端环境中 deepcode 命令不可识别，说明本地入口或环境激活流程不稳，需要纳入 Iteration 0 的安装与启动可靠性修复。

---

## 六、对 DeepCode 的结论（V2 必做）

1. 先补“可用性底座”：安装、命令入口、运行诊断、基础观测。  
2. 再补“核心体验”：CLI 工程化 + Web 控制台化（任务、会话、工件、状态）。  
3. 第三阶段做“可扩展”：MCP/Hook/Skill 插件化。  
4. 最后做“组织级”：策略治理、审计、协作并行与企业部署。

本结论已在后续文档中拆解为可执行迭代。
