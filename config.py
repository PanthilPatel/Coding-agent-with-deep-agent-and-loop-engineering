import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    repo_path: str
    goal: str
    test_cmd: str = "pytest"
    max_iterations: int = 5
    max_seconds: int = 1800
    require_approval: bool = False
    model_name: str = "qwen2.5-coder:7b"
    state_file: str = "state.json"
    llm_provider: Optional[str] = None

    is_remote: bool = False
    local_repo_path: str = ""
    lint_cmd: Optional[str] = None
    skills_dir: Optional[str] = None
    mcp_config_path: Optional[str] = None


    def __post_init__(self) -> None:
        if self.llm_provider is None:
            self.llm_provider = os.environ.get("LLM_PROVIDER", "ollama")

        if self.llm_provider == "ollama_cloud":
            if not os.environ.get("OLLAMA_API_KEY"):
                raise EnvironmentError("OLLAMA_API_KEY is not set.")
        elif self.llm_provider == "ollama":
            pass
        else:
            raise ValueError(f"Unknown llm_provider: {self.llm_provider}")
            
        from utils.git_remote import is_git_url
        self.is_remote = is_git_url(self.repo_path)
        if self.is_remote:
            repo_name = self.repo_path.split("/")[-1]
            if repo_name.endswith(".git"):
                repo_name = repo_name[:-4]
            self.local_repo_path = os.path.abspath(os.path.join("workspace_clones", repo_name))
        else:
            self.repo_path = os.path.abspath(self.repo_path)
            if not os.path.isdir(self.repo_path):
                raise FileNotFoundError(f"Repo path does not exist: {self.repo_path}")
            self.local_repo_path = self.repo_path

