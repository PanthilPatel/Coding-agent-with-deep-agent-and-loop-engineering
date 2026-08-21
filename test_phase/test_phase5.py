"""Phase 5 tests: CLI improvements (interactive mode).

Tests cover:
1. One-shot CLI path preservation (unchanged behavior)
2. Banner displays correct values from registries
3. REPL loop mechanics with scripted input
4. Clean exit and MCP shutdown
"""

import os
import sys
import tempfile
import shutil
from unittest import mock
from unittest.mock import MagicMock, patch, call
import pytest

from config import Config
from cli.interactive import print_banner, run_interactive


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_repo():
    """Create a temporary repository directory."""
    repo_dir = tempfile.mkdtemp()
    # Initialize as git repo to satisfy git operations
    os.system(f"git init {repo_dir} > nul 2>&1")
    yield repo_dir
    shutil.rmtree(repo_dir, ignore_errors=True)


@pytest.fixture
def temp_skills_dir():
    """Create a temporary skills directory with some test skills."""
    skills_dir = tempfile.mkdtemp()
    
    # Create two test skills
    for skill_name in ["test_skill_1", "test_skill_2"]:
        skill_path = os.path.join(skills_dir, skill_name)
        os.makedirs(skill_path, exist_ok=True)
        with open(os.path.join(skill_path, "SKILL.md"), "w") as f:
            f.write(f"# {skill_name}\n\nTest skill content.")
    
    yield skills_dir
    shutil.rmtree(skills_dir, ignore_errors=True)


@pytest.fixture
def temp_mcp_config():
    """Create a temporary MCP config file."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        f.write('''{
            "servers": {
                "test-server-1": {
                    "command": "echo",
                    "args": ["test"]
                },
                "test-server-2": {
                    "command": "echo",
                    "args": ["test2"]
                }
            }
        }''')
    yield path
    os.unlink(path)


@pytest.fixture
def sample_config(temp_repo):
    """Create a sample Config for testing."""
    return Config(
        repo_path=temp_repo,
        goal="test goal",
        test_cmd="pytest",
        max_iterations=5,
        model_name="test-model",
        llm_provider="ollama_cloud",
    )


# ---------------------------------------------------------------------------
# Test 1: One-shot CLI path is preserved
# ---------------------------------------------------------------------------

class TestOneShotCLIPreserved:
    """Verify that the one-shot CLI mode still works identically."""
    
    def test_one_shot_with_goal_calls_run(self, temp_repo, monkeypatch):
        """When --goal is provided, main.py should call controller.loop.run()."""
        # Mock the controller.loop.run to avoid actual execution
        mock_run = MagicMock(return_value=True)
        
        # Mock environment to pass Config validation
        monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
        
        # Simulate command line args with --goal
        test_args = [
            "main.py",
            "--repo", temp_repo,
            "--goal", "make tests pass",
            "--test-cmd", "pytest",
        ]
        
        with patch.object(sys, "argv", test_args):
            with patch("main.run", mock_run):
                with patch.object(sys, "exit") as mock_exit:
                    from main import main
                    main()
        
        # Verify run was called exactly once
        assert mock_run.call_count == 1
        
        # Verify the config passed to run has the correct goal
        called_config = mock_run.call_args[0][0]
        assert called_config.goal == "make tests pass"
        assert called_config.test_cmd == "pytest"
        
        # Verify exit code is 0 (success)
        mock_exit.assert_called_once_with(0)
    
    def test_one_shot_failure_returns_exit_1(self, temp_repo, monkeypatch):
        """When run() returns False, exit code should be 1."""
        mock_run = MagicMock(return_value=False)
        monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
        
        test_args = [
            "main.py",
            "--repo", temp_repo,
            "--goal", "impossible task",
        ]
        
        with patch.object(sys, "argv", test_args):
            with patch("main.run", mock_run):
                with patch.object(sys, "exit") as mock_exit:
                    from main import main
                    main()
        
        mock_exit.assert_called_once_with(1)


# ---------------------------------------------------------------------------
# Test 2: Banner displays correct values
# ---------------------------------------------------------------------------

class TestBanner:
    """Verify the startup banner displays real registry counts."""
    
    def test_banner_shows_tool_count(self, sample_config, capsys):
        """Banner should display the actual count from tool registry."""
        print_banner(sample_config)
        captured = capsys.readouterr()
        
        # Readonly tool registry returns 6 tools: git_status, git_diff, git_log, grep, run_command, list_directory
        assert "Tools:        6" in captured.out

    
    def test_banner_shows_model_name(self, sample_config, capsys):
        """Banner should display the configured model name."""
        print_banner(sample_config)
        captured = capsys.readouterr()
        
        assert "Model:        test-model" in captured.out
    
    def test_banner_shows_repo_path(self, sample_config, capsys):
        """Banner should display the repository path."""
        print_banner(sample_config)
        captured = capsys.readouterr()
        
        assert f"Repository:   {sample_config.local_repo_path}" in captured.out
    
    def test_banner_shows_skill_count_when_skills_exist(
        self, temp_repo, temp_skills_dir, capsys, monkeypatch
    ):
        """Banner should show skill count when skills directory exists."""
        monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
        
        config = Config(
            repo_path=temp_repo,
            goal="test",
            skills_dir=temp_skills_dir,
        )
        
        print_banner(config)
        captured = capsys.readouterr()
        
        # We created 2 test skills in the fixture
        assert "Skills:       2" in captured.out
    
    def test_banner_hides_skill_count_when_no_skills(self, temp_repo, capsys, monkeypatch):
        """Banner should not show Skills line when no skills are available."""
        monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
        
        # Use a nonexistent skills directory
        nonexistent_skills_dir = os.path.join(temp_repo, "nonexistent_skills")
        
        config = Config(
            repo_path=temp_repo,
            goal="test",
            skills_dir=nonexistent_skills_dir,
        )
        
        print_banner(config)
        captured = capsys.readouterr()
        
        # Should not contain Skills line (0 skills)
        assert "Skills:" not in captured.out
    
    def test_banner_shows_mcp_count_when_configured(
        self, temp_repo, temp_mcp_config, capsys, monkeypatch
    ):
        """Banner should show MCP server count when MCP is configured."""
        monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
        
        config = Config(
            repo_path=temp_repo,
            goal="test",
            mcp_config_path=temp_mcp_config,
        )
        
        print_banner(config)
        captured = capsys.readouterr()
        
        # We created 2 MCP servers in the fixture
        assert "MCP Servers:  2" in captured.out
    
    def test_banner_hides_mcp_count_when_not_configured(self, sample_config, capsys):
        """Banner should not show MCP Servers line when MCP is not configured."""
        print_banner(sample_config)
        captured = capsys.readouterr()
        
        assert "MCP Servers:" not in captured.out


# ---------------------------------------------------------------------------
# Test 3: REPL loop mechanics
# ---------------------------------------------------------------------------

class TestREPLLoop:
    """Verify the interactive prompt loop handles input correctly."""
    
    def test_repl_calls_run_for_each_task(self, sample_config):
        """Each /run command should invoke controller.loop.run()."""
        mock_input = mock.Mock(side_effect=["/run task one", "/run task two", "exit"])
        mock_run = MagicMock(return_value=True)
        mock_agent = MagicMock()
        
        with patch("cli.interactive.build_readonly_worker_agent", return_value=mock_agent):
            with patch("builtins.input", mock_input):
                with patch("cli.interactive.run_controller_loop", mock_run):
                    run_interactive(sample_config)
        
        # Should have called run twice (once per /run command)
        assert mock_run.call_count == 2
        
        # Verify each call received a Config with the correct goal
        call_args = [call[0][0] for call in mock_run.call_args_list]
        assert call_args[0].goal == "task one"
        assert call_args[1].goal == "task two"
    
    def test_repl_handles_empty_lines(self, sample_config):
        """Empty lines should be ignored, not passed to run()."""
        mock_input = mock.Mock(side_effect=["", "  ", "/run real task", "exit"])
        mock_run = MagicMock(return_value=True)
        mock_agent = MagicMock()
        
        with patch("cli.interactive.build_readonly_worker_agent", return_value=mock_agent):
            with patch("builtins.input", mock_input):
                with patch("cli.interactive.run_controller_loop", mock_run):
                    run_interactive(sample_config)
        
        # Only "/run real task" should invoke run
        assert mock_run.call_count == 1
        assert mock_run.call_args[0][0].goal == "real task"
    
    def test_repl_exits_on_quit_command(self, sample_config):
        """The 'quit' command should exit the loop cleanly."""
        mock_input = mock.Mock(side_effect=["/run task one", "quit"])
        mock_run = MagicMock(return_value=True)
        mock_agent = MagicMock()
        
        with patch("cli.interactive.build_readonly_worker_agent", return_value=mock_agent):
            with patch("builtins.input", mock_input):
                with patch("cli.interactive.run_controller_loop", mock_run):
                    run_interactive(sample_config)
        
        # Should have run once before quit
        assert mock_run.call_count == 1
    
    def test_repl_exits_on_exit_command(self, sample_config):
        """The 'exit' command should exit the loop cleanly."""
        mock_input = mock.Mock(side_effect=["/run task one", "exit"])
        mock_run = MagicMock(return_value=True)
        mock_agent = MagicMock()
        
        with patch("cli.interactive.build_readonly_worker_agent", return_value=mock_agent):
            with patch("builtins.input", mock_input):
                with patch("cli.interactive.run_controller_loop", mock_run):
                    run_interactive(sample_config)
        
        assert mock_run.call_count == 1
    
    def test_repl_handles_eof(self, sample_config):
        """EOFError (Ctrl+D) should exit cleanly."""
        mock_input = mock.Mock(side_effect=["/run task one", EOFError()])
        mock_run = MagicMock(return_value=True)
        mock_agent = MagicMock()
        
        with patch("cli.interactive.build_readonly_worker_agent", return_value=mock_agent):
            with patch("builtins.input", mock_input):
                with patch("cli.interactive.run_controller_loop", mock_run):
                    run_interactive(sample_config)
        
        # Should complete the one task before EOF
        assert mock_run.call_count == 1
    
    def test_repl_handles_keyboard_interrupt(self, sample_config):
        """KeyboardInterrupt (Ctrl+C) should exit cleanly."""
        mock_input = mock.Mock(side_effect=["/run task one", KeyboardInterrupt()])
        mock_run = MagicMock(return_value=True)
        mock_agent = MagicMock()
        
        with patch("cli.interactive.build_readonly_worker_agent", return_value=mock_agent):
            with patch("builtins.input", mock_input):
                with patch("cli.interactive.run_controller_loop", mock_run):
                    run_interactive(sample_config)
        
        assert mock_run.call_count == 1
    
    def test_repl_case_insensitive_exit_commands(self, sample_config):
        """Exit commands should be case-insensitive."""
        for exit_cmd in ["EXIT", "Exit", "QUIT", "Quit", "QuIt"]:
            mock_input = mock.Mock(side_effect=[exit_cmd])
            mock_run = MagicMock(return_value=True)
            mock_agent = MagicMock()
            
            with patch("cli.interactive.build_readonly_worker_agent", return_value=mock_agent):
                with patch("builtins.input", mock_input):
                    with patch("cli.interactive.run_controller_loop", mock_run):
                        run_interactive(sample_config)
            
            # Should not call run (exit immediately)
            assert mock_run.call_count == 0


# ---------------------------------------------------------------------------
# Test 4: MCP shutdown handling
# ---------------------------------------------------------------------------

class TestMCPShutdown:
    """Verify MCP connections are cleaned up on exit."""
    
    def test_mcp_registry_closed_on_exit(self, temp_repo, temp_mcp_config, monkeypatch):
        """When MCP is configured, registry.close() should be called on exit."""
        monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
        
        config = Config(
            repo_path=temp_repo,
            goal="test",
            mcp_config_path=temp_mcp_config,
        )
        
        mock_input = mock.Mock(side_effect=["exit"])
        mock_registry_close = mock.AsyncMock()
        mock_agent = MagicMock()
        
        with patch("cli.interactive.build_readonly_worker_agent", return_value=mock_agent):
            with patch("builtins.input", mock_input):
                with patch("mcp_agent.registry.MCPRegistry.close", mock_registry_close):
                    with patch("mcp_agent.registry.MCPRegistry.initialize", mock.AsyncMock()):
                        run_interactive(config)
        
        # Verify close was called
        mock_registry_close.assert_called_once()
    
    def test_shutdown_handles_mcp_errors_gracefully(
        self, temp_repo, temp_mcp_config, monkeypatch, capsys
    ):
        """If MCP close() fails, it shouldn't crash the exit process."""
        monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
        
        config = Config(
            repo_path=temp_repo,
            goal="test",
            mcp_config_path=temp_mcp_config,
        )
        
        mock_input = mock.Mock(side_effect=["exit"])
        mock_registry_close = mock.AsyncMock(side_effect=RuntimeError("MCP Close Failed"))
        mock_agent = MagicMock()
        
        with patch("cli.interactive.build_readonly_worker_agent", return_value=mock_agent):
            with patch("builtins.input", mock_input):
                with patch("mcp_agent.registry.MCPRegistry.close", mock_registry_close):
                    with patch("mcp_agent.registry.MCPRegistry.initialize", mock.AsyncMock()):
                        run_interactive(config)
        
        # Just verify it completes without error
    
    def test_no_mcp_shutdown_when_not_configured(self, sample_config):
        """When MCP is not configured, no shutdown should be attempted."""
        mock_input = mock.Mock(side_effect=["exit"])
        mock_run = MagicMock(return_value=True)
        
        # This should not raise, even though no MCP registry exists
        with patch("builtins.input", mock_input):
            with patch("cli.interactive.run_controller_loop", mock_run):
                run_interactive(sample_config)
        
        # Just verify it completes without error


# ---------------------------------------------------------------------------
# Test 5: State isolation per task
# ---------------------------------------------------------------------------

class TestStateIsolation:
    """Verify each task gets a fresh state."""
    
    def test_each_task_creates_new_config(self, sample_config):
        """Each /run task should create a new Config instance with updated goal."""
        mock_input = mock.Mock(side_effect=["/run task A", "/run task B", "exit"])
        mock_run = MagicMock(return_value=True)
        mock_agent = MagicMock()
        
        with patch("cli.interactive.build_readonly_worker_agent", return_value=mock_agent):
            with patch("builtins.input", mock_input):
                with patch("cli.interactive.run_controller_loop", mock_run):
                    run_interactive(sample_config)
        
        # Get the Config objects passed to each run call
        configs = [call[0][0] for call in mock_run.call_args_list]
        
        # Each config should have the correct goal
        assert configs[0].goal == "task A"
        assert configs[1].goal == "task B"
        
        # They should be separate instances
        assert configs[0] is not configs[1]
        
        # But share the same base settings
        assert configs[0].test_cmd == configs[1].test_cmd
        assert configs[0].model_name == configs[1].model_name


# ---------------------------------------------------------------------------
# Test 6: Integration with existing CLI flags
# ---------------------------------------------------------------------------

class TestCLIFlagIntegration:
    """Verify interactive mode respects all existing CLI flags."""
    
    def test_interactive_mode_triggered_when_no_goal(self, temp_repo, monkeypatch):
        """When --goal is omitted, should enter interactive mode."""
        monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
        
        test_args = [
            "main.py",
            "--repo", temp_repo,
            # No --goal argument
        ]
        
        mock_run_interactive = MagicMock()
        mock_input = mock.Mock(side_effect=["exit"])  # Exit immediately
        
        with patch.object(sys, "argv", test_args):
            with patch("cli.interactive.run_interactive", mock_run_interactive):
                with patch("builtins.input", mock_input):
                    with patch.object(sys, "exit"):
                        from main import main
                        main()
        
        # Verify interactive mode was invoked
        assert mock_run_interactive.call_count == 1
    
    def test_interactive_preserves_all_flags(self, temp_repo, monkeypatch):
        """All CLI flags should be passed through to the config template."""
        monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
        
        test_args = [
            "main.py",
            "--repo", temp_repo,
            "--test-cmd", "custom-test",
            "--max-iterations", "20",
            "--max-seconds", "3600",
            "--model", "custom-model",
            "--lint-cmd", "flake8",
            # No --goal
        ]
        
        mock_run_interactive = MagicMock()
        mock_input = mock.Mock(side_effect=["exit"])
        
        with patch.object(sys, "argv", test_args):
            with patch("cli.interactive.run_interactive", mock_run_interactive):
                with patch("builtins.input", mock_input):
                    with patch.object(sys, "exit"):
                        from main import main
                        main()
        
        # Get the config template passed to run_interactive
        config_template = mock_run_interactive.call_args[0][0]
        
        assert config_template.test_cmd == "custom-test"
        assert config_template.max_iterations == 20
        assert config_template.max_seconds == 3600
        assert config_template.model_name == "custom-model"
        assert config_template.lint_cmd == "flake8"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
