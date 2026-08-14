# Autonomous Coding Agent (Deep Agent + Loop Engineering)

An autonomous "fix-until-green" coding agent built around a robust multi-phase loop. Give it a repository target and a goal (e.g. "fix all failing unit tests"), and the agent will inspect code, select domain skills, execute edits, verify results via test/lint execution, and iteratively resolve issues until green or safety limits are reached.

---

## 🌟 Features & Architecture

```
                                 +-------------------------+
                                 |       User / CLI        |
                                 +------------+------------+
                                              |
                                              v
                                 +-------------------------+
                                 |     Controller Loop     |
                                 +------------+------------+
                                              |
                     +------------------------+------------------------+
                     |                        |                        |
                     v                        v                        v
        +-------------------------+  +------------------+  +-----------------------+
        |   Skill Loader & Map    |  | State / Memory   |  |   Tool Registry &     |
        |  (Deterministic select) |  |   (state.json)   |  |   MCP Integrations    |
        +-------------------------+  +------------------+  +-----------------------+
                     |                        |                        |
                     +------------------------+------------------------+
                                              |
                                              v
                                 +-------------------------+
                                 |  Worker (Deep Agent)    |
                                 | (Filesystem & Reviewer) |
                                 +------------+------------+
                                              |
                                              v
                                 +-------------------------+
                                 |   Evaluator & Router    |
                                 | (Pass/Fail & Stopping)  |
                                 +-------------------------+
```

- **Controller Loop (`controller/loop.py`)**: Central orchestrator managing the run lifecycle, skill selection, worker turns, execution verification, git tracking, state persistence, and safety limits.
- **Evaluator & Router (`controller/evaluator.py`, `controller/router.py`)**: Decoupled, objective verification scoring and safety decision logic (handles success, max iterations, timeout, user rejection, and repeated failure loops).
- **Worker Deep Agent (`agents/worker.py`)**: Powered by LangGraph/DeepAgents and Ollama Cloud, equipped with file manipulation, reviewer sub-agent capabilities, and custom tool bindings.
- **Skill Engine (`skills/`)**: Dynamic procedural guidance engine using keyword matching to inject domain-specific task guides (`SKILL.md`) into instructions.
- **MCP Client & Registry (`mcp_agent/`)**: Model Context Protocol integration supporting stdio transport server connections, environment variable interpolation, and graceful degradation.
- **Tool Registry (`tools/`)**: LangChain-compatible tools providing structured test execution (`run_tests`), git status/diff/log, and safe commit operations with approval gates.
- **Dual Execution Modes (`cli/interactive.py`, `main.py`)**: Supports both one-shot goal execution and interactive REPL mode.

---

## 📋 Prerequisites & Setup

### Environment Requirements
- **Python 3.10+** (Python 3.14 recommended)
- **Git** installed and available on `PATH`
- **Ollama Cloud API Key** (`OLLAMA_API_KEY`)

### Installation

1. **Clone or navigate to the repository:**
   ```bash
   cd "Coding agent with deep agents and loop engineering"
   ```

2. **Set up a virtual environment:**
   ```bash
   python -m venv venv
   # On Linux/macOS:
   source venv/bin/activate
   # On Windows (PowerShell):
   .\venv\Scripts\Activate.ps1
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**
   Create a `.env` file in the project root:
   ```env
   OLLAMA_API_KEY=your_ollama_api_key_here
   LLM_PROVIDER=ollama_cloud
   # Optional: GitHub token for authenticated remote cloning/pushing
   GITHUB_TOKEN=your_github_token_here
   ```

---

## 🚀 Usage

### 1. One-Shot Mode
Execute a single goal against a target repository and exit:

```bash
python main.py --repo /path/to/target/repo --goal "fix all failing unit tests"
```

#### Advanced One-Shot Options:
```bash
python main.py \
  --repo "https://github.com/owner/repo.git" \
  --goal "fix bug in user login" \
  --test-cmd "pytest tests/ -v" \
  --lint-cmd "flake8 ." \
  --max-iterations 10 \
  --max-seconds 1800 \
  --require-approval \
  --model "gemma4" \
  --mcp-config-path "mcp.json"
```

### 2. Interactive REPL Mode
Omit the `--goal` argument to launch the interactive prompt session:

```bash
python main.py --repo /path/to/target/repo
```

```text
============================================================
                     Coding Agent
============================================================
Repository:   D:\path\to\target\repo
Model:        gemma4
Tools:        5
Skills:       5
============================================================

Type a task description to execute, or 'exit'/'quit' to stop.

> fix failing math test
```

---

## ⚙️ Command-Line Flags Reference

| Flag | Default | Description |
|------|---------|-------------|
| `--repo` | *(Required)* | Path to local repository directory or remote Git URL. |
| `--goal` | `None` | Task description. If omitted, enters interactive REPL mode. |
| `--test-cmd` | `pytest` | Test execution command. |
| `--lint-cmd` | `None` | Optional linting/type-checking command (e.g. `flake8 .`). |
| `--max-iterations` | `10` | Maximum controller iterations allowed. |
| `--max-seconds` | `1800` | Maximum wall-clock execution time (in seconds). |
| `--require-approval` | `False` | Prompt for user confirmation before committing changes. |
| `--model` | `gemma4` | Model identifier to pass to ChatOllama. |
| `--llm-provider` | `ollama_cloud` | LLM provider backend identifier. |
| `--skills-dir` | `None` | Custom path to skills folder (defaults to `skills/`). |
| `--mcp-config-path` | `None` | Path to MCP server configuration JSON file (`mcp.json`). |
| `--verbose` | `False` | Enables verbose LangChain debug output. |

---

## 🔌 Model Context Protocol (MCP) Configuration

To connect external tools via MCP, pass a JSON configuration file via `--mcp-config-path`:

```json
{
  "servers": {
    "filesystem_extra": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"],
      "env": {
        "CUSTOM_VAR": "${API_TOKEN}"
      }
    }
  }
}
```

*Note: Environment variables in `${VAR}` or `$VAR` format within `mcp.json` are automatically interpolated from the system environment.*

---

## 🧪 Testing & Verification

Run the comprehensive unit, characterization, integration, and end-to-end smoke test suite:

```bash
python -m pytest test_agent.py test_phase/ -v
```

All 247 tests cover:
- Core state serialization & backward compatibility
- Tool registry & safety approval gates
- Skill discovery, loading, and deterministic selection
- MCP config parsing, interpolation, and graceful degradation
- CLI flags & REPL banner/session isolation
- Evaluator & Router policy boundaries
- Full end-to-end smoke tests (real Git + real subprocesses + stubbed LLM)

---

## 🛡️ Scope & Boundaries

- **Local Execution First**: All code modifications are performed on dedicated Git work branches (`auto-agent-work`).
- **No Direct Master Commits**: The controller isolates agent work from main branches.
- **Graceful Degradation**: Missing optional MCP servers or skills directories trigger warnings rather than breaking application execution.
