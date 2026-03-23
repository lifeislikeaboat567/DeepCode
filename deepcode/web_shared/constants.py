"""Static UI catalogs and navigation metadata."""

from __future__ import annotations

MODEL_PROVIDER_OPTIONS = ["openai", "ollama", "gemini", "github_copilot", "mock"]

NAV_ITEMS = [
    {"id": "dashboard", "icon": ":material/space_dashboard:", "label_key": "nav.dashboard"},
    {"id": "chat", "icon": ":material/chat:", "label_key": "nav.chat"},
    {"id": "task_center", "icon": ":material/lan:", "label_key": "nav.task_center"},
    {"id": "artifact_center", "icon": ":material/inventory_2:", "label_key": "nav.artifact_center"},
    {"id": "platform_bridge", "icon": ":material/smartphone:", "label_key": "nav.platform_bridge"},
    {"id": "extensions", "icon": ":material/extension:", "label_key": "nav.extensions"},
    {"id": "model_studio", "icon": ":material/tune:", "label_key": "nav.model_studio"},
    {"id": "governance", "icon": ":material/verified_user:", "label_key": "nav.governance"},
    {"id": "about", "icon": ":material/info:", "label_key": "nav.about"},
]

SKILL_SITE_CATALOG = [
    {
        "id": "skill-clawhub",
        "name": "ClawHub",
        "url": "https://clawhub.ai",
        "description": "Community marketplace for reusable agent skills and prompts.",
        "description_zh": "社区化技能与提示模板市场，可用于发现可复用能力。",
        "tags": ["skills", "market", "community"],
    },
    {
        "id": "skill-skillhub",
        "name": "SkillHub",
        "url": "https://skillhub.ai",
        "description": "Directory for discovering skill packs and prompt toolkits.",
        "description_zh": "技能包与提示工具包目录，可快速发现可安装资源。",
        "tags": ["skills", "directory", "hub"],
    },
    {
        "id": "skill-awesome-chatgpt-prompts",
        "name": "Awesome ChatGPT Prompts",
        "url": "https://github.com/f/awesome-chatgpt-prompts",
        "description": "Prompt and skill idea collection for rapid task setup.",
        "description_zh": "高频提示词与技能灵感集合，适合快速搭建任务模板。",
        "tags": ["prompts", "skills", "community"],
    },
    {
        "id": "skill-awesome-llm-apps",
        "name": "Awesome LLM Apps",
        "url": "https://github.com/Shubhamsaboo/awesome-llm-apps",
        "description": "Examples and reusable patterns for coding assistant abilities.",
        "description_zh": "覆盖编程助手场景的案例与可复用模式集合。",
        "tags": ["examples", "patterns", "skills"],
    },
    {
        "id": "skill-langchain-hub",
        "name": "LangChain Hub",
        "url": "https://smith.langchain.com/hub",
        "description": "Browse and reuse prompt templates for common engineering tasks.",
        "description_zh": "浏览并复用常见工程任务的提示模板。",
        "tags": ["hub", "templates", "prompt"],
    },
]

MCP_SITE_CATALOG = [
    {
        "id": "mcp-official",
        "name": "Model Context Protocol",
        "url": "https://modelcontextprotocol.io/",
        "description": "Official MCP docs and integration guides.",
        "description_zh": "MCP 官方文档与集成指南。",
        "tags": ["mcp", "docs", "official"],
    },
    {
        "id": "mcp-so",
        "name": "MCP.so",
        "url": "https://mcp.so/",
        "description": "Discover MCP servers and ecosystem resources.",
        "description_zh": "发现 MCP 服务与周边生态资源。",
        "tags": ["directory", "ecosystem", "mcp"],
    },
    {
        "id": "mcp-servers-org",
        "name": "MCP Servers",
        "url": "https://mcpservers.org/",
        "description": "Community-curated MCP server index and links.",
        "description_zh": "社区整理的 MCP 服务目录与索引。",
        "tags": ["catalog", "servers", "mcp"],
    },
]

MCP_TEMPLATE_CATALOG = [
    {
        "id": "tpl-filesystem",
        "name": "Filesystem Server",
        "name_zh": "文件系统服务",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
        "description": "Local project file operations through MCP.",
        "description_zh": "通过 MCP 访问当前项目的本地文件操作。",
    },
    {
        "id": "tpl-github",
        "name": "GitHub Server",
        "name_zh": "GitHub 服务",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "description": "GitHub repo and issue access through MCP.",
        "description_zh": "通过 MCP 访问 GitHub 仓库与 Issue。",
    },
    {
        "id": "tpl-brave-search",
        "name": "Brave Search Server",
        "name_zh": "Brave Search 服务",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "description": "Web search as a tool through MCP.",
        "description_zh": "把 Web 搜索能力作为 MCP 工具接入。",
    },
]
