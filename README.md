# 🤖 DeepCode Agent

> AI-powered software engineering assistant — plan, code, review, and test with a single command.

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## 📖 Documentation

| Document | Description |
|----------|-------------|
| [AI Agent 调研报告](docs/01_research_report.md) | Survey of popular AI agents (AutoGen, CrewAI, LangGraph, Devin, SWE-agent…) |
| [功能设计与技术路线](docs/02_design_document.md) | Feature specification and technical architecture |
| [软件开发文档](docs/03_development_document.md) | Phase-by-phase development plan with acceptance criteria |

---

## ✨ Features

- 🧠 **Multi-Agent Architecture** — Orchestrator, Coder, Reviewer, and Tester agents collaborate
- 🔄 **Full Loop Automation** — Plan → Code → Execute → Review → Test
- 🛡️ **Secure Execution** — Sandboxed subprocess with timeout and command allow-list
- 💾 **Persistent Memory** — SQLite sessions + ChromaDB vector memory
- 🌐 **REST API** — FastAPI with automatic OpenAPI documentation
- 💬 **CLI** — Rich interactive command-line interface
- 🖥️ **Web UI** — Streamlit-powered browser interface
- 🔌 **Multi-LLM** — OpenAI, Ollama (local), or mock for testing

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- An OpenAI API key (or a local Ollama instance)

### Installation

```bash
# Clone the repository
git clone https://github.com/lifeislikeaboat567/DeepCode.git
cd DeepCode

# Install in development mode
pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env and set DEEPCODE_LLM_API_KEY=sk-...
```

### CLI Usage

```bash
# Interactive chat
deepcode chat

# Run a single task (multi-agent workflow)
deepcode run "Write a Python class for a binary search tree"

# Stream the agent's reasoning
deepcode run --stream "Build a REST API for a todo list"

# Start the REST API server
deepcode serve

# Launch the Web UI
deepcode ui
```

### Using the Mock Provider (no API key required)

```bash
DEEPCODE_LLM_PROVIDER=mock deepcode chat
```

### Local Models with Ollama

```bash
# Pull a model
ollama pull llama3

# Configure DeepCode to use it
DEEPCODE_LLM_PROVIDER=ollama DEEPCODE_LLM_MODEL=llama3 deepcode chat
```

---

## 🐳 Docker

```bash
# Set your API key in .env first
docker-compose up

# API available at: http://localhost:8000/docs
# Web UI available at: http://localhost:8501
```

---

## 🔌 REST API

Start the server with `deepcode serve`, then visit **http://localhost:8000/docs** for the interactive API documentation.

### Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/health` | Health check |
| `POST` | `/api/v1/chat` | Single-turn chat with the agent |
| `GET` | `/api/v1/chat/stream` | Streaming chat (SSE) |
| `POST` | `/api/v1/tasks` | Create an orchestrated task (async) |
| `GET` | `/api/v1/tasks/{id}` | Poll task status |
| `POST` | `/api/v1/sessions` | Create a session |
| `GET` | `/api/v1/sessions` | List sessions |

### Example: Chat

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Write a Python function to reverse a string"}'
```

### Example: Orchestrated Task

```bash
# Create task
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{"task": "Build a calculator class with add, subtract, multiply, divide"}'

# Poll for result
curl http://localhost:8000/api/v1/tasks/<task-id>
```

---

## 🏗️ Architecture

```
deepcode/
├── agents/          # Agent implementations (Base ReAct + Orchestrator)
├── tools/           # Tool suite (code executor, file manager, shell)
├── memory/          # Short-term (in-memory) + long-term (ChromaDB)
├── storage/         # Session persistence (SQLite via aiosqlite)
├── api/             # FastAPI REST API
│   └── routes/      # Chat, sessions, tasks, health
├── llm/             # LLM abstraction (OpenAI, Ollama, Mock)
├── ui/              # Streamlit web interface
├── cli.py           # Click CLI entry point
└── config.py        # Pydantic Settings configuration
```

---

## 🧪 Testing

```bash
# Run all tests
pytest

# With coverage report
pytest --cov=deepcode --cov-report=term-missing

# Run a specific module
pytest tests/test_tools.py -v
```

---

## ⚙️ Configuration

All settings use the `DEEPCODE_` prefix and can be set in `.env` or as environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `DEEPCODE_LLM_PROVIDER` | `openai` | LLM provider: `openai`, `ollama`, `mock` |
| `DEEPCODE_LLM_MODEL` | `gpt-4o-mini` | Model name |
| `DEEPCODE_LLM_API_KEY` | _(required for openai)_ | API key |
| `DEEPCODE_LLM_BASE_URL` | _(empty)_ | Custom API base URL |
| `DEEPCODE_API_PORT` | `8000` | API server port |
| `DEEPCODE_MAX_EXECUTION_TIME` | `30` | Code execution timeout (seconds) |
| `DEEPCODE_ALLOWED_SHELLS` | `ls,cat,grep,...` | Whitelisted shell commands |
| `DEEPCODE_MAX_HISTORY_MESSAGES` | `50` | Conversation history window |
| `DEEPCODE_DATA_DIR` | `~/.deepcode` | Persistent data directory |

---

## 📄 License

MIT © DeepCode Team