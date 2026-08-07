import os
import datetime
from deepagents import create_deep_agent, SubAgent
from deepagents.backends import FilesystemBackend
from langchain_ollama import ChatOllama
from langchain_core.callbacks import BaseCallbackHandler

class TerminalLogCallbackHandler(BaseCallbackHandler):
    def __init__(self, log_dir: str = "agent_logs"):
        super().__init__()
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception:
            pass
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(log_dir, f"agent_{timestamp}.log")

    def _log_to_file(self, text: str):
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def on_llm_start(self, serialized, prompts, **kwargs):
        msg = "[agent] Calling LLM to analyze and plan..."
        print(msg)
        self._log_to_file(msg)

    def on_tool_start(self, serialized, input_str, **kwargs):
        name = serialized.get("name", "tool")
        print(f"[agent] -> Executing tool '{name}'")
        self._log_to_file(f"[agent] -> Executing tool '{name}' with input: {input_str}")

    def on_tool_end(self, output, **kwargs):
        out_str = str(output).strip()
        self._log_to_file(f"[agent] <- Tool returned: {out_str}")

REVIEWER_SUBAGENT = SubAgent(
    name="reviewer",
    description=(
        "Reviews a code diff for correctness, style, and whether it "
        "actually addresses the reported test failure. Does not edit "
        "files itself; only comments."
    ),
    system_prompt=(
        "You are a careful code reviewer. You will be given a diff and "
        "the test failure it was meant to fix. Check whether the diff "
        "plausibly fixed the failure, whether it introduces obvious bugs "
        "or regressions, and whether it follows the existing code style. "
        "Respond with either 'APPROVE' followed by a one-line reason, or "
        "'REJECT' followed by a specific, actionable reason."
    ),
)

WORKER_SYSTEM_PROMPT = """You are an autonomous coding agent working inside a 
real repository on idsk. Your job is to achieve the given goal by reading and editing files directly.

Rules:
- Always start by writing a short todo list breaking the goal into concrete
  steps, and keep it updated as you make progress.
- Read the relevant files before editing them. Do not guess at code you 
  have not read.
- Make the smallest change that could plausibly fix a reported issue.
- After editing, briefly summarize what you changed and why, in one or two
  sentences, so the controller can log it.
- If you are given a previous failed attempt and told to change strategy,
  do not repeat the same fix - analyze why it failed and try a 
  meaningfully different approach.
- You do not run tests yourself; the controller will run them after you
  finish and report the results back to you on the next turn.
"""

def _build_model(model_name: str, llm_provider: str = "ollama_cloud"):
    return ChatOllama(
        model=model_name,
        base_url="https://ollama.com",
        headers={"Authorization": f"Bearer {os.environ['OLLAMA_API_KEY']}"}
    )

def build_worker_agent(repo_path: str, model_name: str = "gemma4", llm_provider: str = "ollama_cloud"):
    """Construct the deep agent worker, scoped to 'repo_path'.

    Returns a LangGraph-compiled agent that can be invoked with 
    'agent.invoke({"messages": [...]})'.
    """
    model = _build_model(model_name, llm_provider)
    backend = FilesystemBackend(root_dir=repo_path)
     
    agent = create_deep_agent(
        model=model,
        system_prompt=WORKER_SYSTEM_PROMPT,
        subagents=[REVIEWER_SUBAGENT],
        backend=backend,
    )
    return agent

def run_worker_turn(agent, instruction: str) -> str:
    """Invoke the worker agent with a single instruction and return the 
    text of its final response.
    """
    from langgraph.errors import GraphRecursionError
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": instruction}]},
            config={
                "callbacks": [TerminalLogCallbackHandler()],
                "recursion_limit": 25
            }
        )
        messages = result.get("messages", [])
        if not messages:
            return ""
        last = messages[-1]
        if isinstance(last, dict):
            return last.get("content", "") or ""
        return getattr(last, "content", "") or ""
    except GraphRecursionError:
        print("\n[worker] Warning: Agent hit recursion limit of 25 steps (potential loop). Aborting turn.")
        return "Agent hit recursion limit of 25 steps due to repeating operations."
    except Exception as e:
        print(f"\n[worker] Error during execution: {e}")
        return f"Agent failed with error: {e}"


