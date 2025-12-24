"""
GitHub Copilot CLI Client Wrapper
==================================

Python wrapper that shells out to the GitHub Copilot CLI.
Provides a drop-in replacement for ClaudeSDKClient with the same interface.

This uses subprocess to call the `copilot` command and parse its output.
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


class CopilotCLIClient:
    """
    Python client for GitHub Copilot CLI via subprocess.
    
    Provides a similar interface to ClaudeSDKClient but shells out to
    the `copilot` CLI command and parses the output.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        working_directory: str | None = None,
        available_tools: list[str] | None = None,
        mcp_servers: dict[str, Any] | None = None,
        system_message: str | None = None,
    ):
        """
        Initialize Copilot CLI client.

        Args:
            model: Model to use (gpt-4o, gpt-4-turbo, etc.)
            working_directory: Project directory
            available_tools: List of allowed tools
            mcp_servers: MCP server configurations
            system_message: System prompt
        """
        self.model = model
        self.working_directory = working_directory or str(Path.cwd())
        self.available_tools = available_tools or []
        self.mcp_servers = mcp_servers or {}
        self.system_message = system_message
        self._session_file = None
        self._process = None
        
        logger.debug(
            f"Initialized CopilotCLIClient: model={model}, cwd={self.working_directory}"
        )

        # Verify copilot CLI is installed
        self._verify_copilot_installed()

    def _verify_copilot_installed(self) -> None:
        """Verify that the copilot CLI is installed and accessible."""
        try:
            result = subprocess.run(
                ["copilot", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "GitHub Copilot CLI not installed or not in PATH.\n"
                    "Install with: npm install -g @github/copilot\n"
                    "Or via Homebrew: brew install copilot-cli"
                )
            logger.debug(f"Copilot CLI version: {result.stdout.strip()}")
        except FileNotFoundError:
            raise RuntimeError(
                "GitHub Copilot CLI not installed.\n"
                "Install with: npm install -g @github/copilot\n"
                "Or via Homebrew: brew install copilot-cli"
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("Copilot CLI command timed out")

    async def create_session(self) -> str:
        """
        Create a new session with copilot CLI.
        
        Since copilot CLI doesn't maintain sessions like Claude SDK,
        we'll use a temporary file to store conversation history.

        Returns:
            Session ID (file path)
        """
        self._session_file = tempfile.NamedTemporaryFile(
            mode='w+',
            suffix='.json',
            delete=False,
            prefix='copilot_session_'
        )
        session_data = {
            "model": self.model,
            "working_directory": self.working_directory,
            "messages": [],
            "system_message": self.system_message,
        }
        json.dump(session_data, self._session_file)
        self._session_file.flush()
        
        logger.info(f"Created session file: {self._session_file.name}")
        return self._session_file.name

    async def query(self, prompt: str) -> None:
        """
        Send a query via copilot CLI.

        Args:
            prompt: The prompt to send
        """
        if not self._session_file:
            await self.create_session()

        logger.debug(f"Sending query to copilot CLI")
        # Store the prompt for retrieval by receive_response
        self._current_prompt = prompt

    async def receive_response(self) -> AsyncIterator[Any]:
        """
        Receive response from copilot CLI by shelling out to the command.

        Yields:
            Message objects with events from copilot
        """
        if not self._session_file or not hasattr(self, '_current_prompt'):
            raise RuntimeError("No active session. Call query() first.")

        # Read current session
        self._session_file.seek(0)
        session_data = json.load(self._session_file)
        
        # Add user message to history
        session_data["messages"].append({
            "role": "user",
            "content": self._current_prompt
        })

        # Create a temporary file for the prompt
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as prompt_file:
            prompt_file.write(self._current_prompt)
            prompt_file.flush()
            prompt_path = prompt_file.name

        try:
            # Build copilot CLI command
            # Note: The actual copilot CLI might have different flags
            # This is a simplified version - adjust based on actual CLI interface
            cmd = [
                "copilot",
                "--prompt", prompt_path,
                "--model", self.model,
                "--cwd", self.working_directory,
            ]
            
            # If system message provided, add it
            if self.system_message:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as sys_file:
                    sys_file.write(self.system_message)
                    sys_file.flush()
                    cmd.extend(["--system", sys_file.name])
                    sys_msg_path = sys_file.name
            else:
                sys_msg_path = None

            logger.debug(f"Running copilot CLI: {' '.join(cmd)}")
            
            # Run copilot CLI and capture output
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.working_directory,
            )
            self._process = process

            # Stream output line by line
            response_text = ""
            for line in iter(process.stdout.readline, ''):
                if line:
                    response_text += line
                    # Yield text blocks as we receive them
                    yield self._create_text_message(line.rstrip())

            # Wait for process to complete
            process.wait()
            
            if process.returncode != 0:
                error_output = process.stderr.read()
                logger.error(f"Copilot CLI error: {error_output}")
                yield self._create_error_message(f"CLI error: {error_output}")
            
            # Update session with response
            session_data["messages"].append({
                "role": "assistant",
                "content": response_text
            })
            
            # Save session
            self._session_file.seek(0)
            self._session_file.truncate()
            json.dump(session_data, self._session_file)
            self._session_file.flush()

        finally:
            # Cleanup temp files
            try:
                os.unlink(prompt_path)
                if sys_msg_path:
                    os.unlink(sys_msg_path)
            except Exception as e:
                logger.debug(f"Failed to cleanup temp files: {e}")

    def _create_text_message(self, text: str) -> Any:
        """
        Create a text message object compatible with Claude SDK format.

        Args:
            text: The text content

        Returns:
            Message object
        """
        class Message:
            def __init__(self, text_content):
                self.type = "message"
                self.content = [TextBlock(text_content)]

        class TextBlock:
            def __init__(self, text):
                self.text = text

        return Message(text)

    def _create_error_message(self, error: str) -> Any:
        """Create an error message object."""
        class ErrorMessage:
            def __init__(self, error_text):
                self.type = "error"
                self.error = error_text

        return ErrorMessage(error)

    async def abort(self) -> None:
        """Abort the current CLI process."""
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                logger.info("Aborted copilot CLI process")
            except Exception as e:
                logger.error(f"Failed to abort process: {e}")

    async def close(self) -> None:
        """Close the session and cleanup."""
        await self.abort()
        
        if self._session_file:
            try:
                self._session_file.close()
                os.unlink(self._session_file.name)
                logger.info(f"Closed and removed session file")
            except Exception as e:
                logger.error(f"Failed to cleanup session file: {e}")
            finally:
                self._session_file = None

    def __del__(self):
        """Cleanup on deletion."""
        if self._session_file:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self.close())
                else:
                    loop.run_until_complete(self.close())
            except Exception:
                logger.debug("Could not close session in __del__")
