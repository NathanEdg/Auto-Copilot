"""
GitHub Copilot Client Wrapper
==============================

Python wrapper for the GitHub Copilot Bridge Server.
Provides a drop-in replacement for ClaudeSDKClient with the same interface.
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, AsyncIterator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class CopilotBridgeClient:
    """
    Python client for GitHub Copilot Bridge Server.
    
    Provides a similar interface to ClaudeSDKClient but communicates with
    the Node.js bridge server that wraps @github/copilot SDK.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        working_directory: str | None = None,
        available_tools: list[str] | None = None,
        mcp_servers: dict[str, Any] | None = None,
        system_message: str | None = None,
        bridge_url: str | None = None,
    ):
        """
        Initialize Copilot bridge client.

        Args:
            model: Model to use (gpt-4o, gpt-4-turbo, etc.)
            working_directory: Project directory
            available_tools: List of allowed tools
            mcp_servers: MCP server configurations
            system_message: System prompt
            bridge_url: URL of the bridge server (default: http://localhost:9545)
        """
        self.model = model
        self.working_directory = working_directory or str(Path.cwd())
        self.available_tools = available_tools or []
        self.mcp_servers = mcp_servers or {}
        self.system_message = system_message
        self.bridge_url = bridge_url or os.environ.get(
            "COPILOT_BRIDGE_URL", "http://localhost:9545"
        )
        self.session_id: str | None = None
        self.bridge_process: subprocess.Popen | None = None

        # Configure HTTP session with retries
        self.session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        logger.debug(
            f"Initialized CopilotBridgeClient: bridge_url={self.bridge_url}, model={model}"
        )

    def _ensure_bridge_running(self) -> None:
        """Ensure the bridge server is running, start it if needed."""
        try:
            response = self.session.get(f"{self.bridge_url}/health", timeout=2)
            if response.status_code == 200:
                logger.debug("Bridge server is already running")
                return
        except requests.exceptions.ConnectionError:
            logger.info("Bridge server not running, attempting to start...")

        # Start the bridge server
        bridge_dir = Path(__file__).parent.parent / "copilot-bridge"
        if not bridge_dir.exists():
            raise RuntimeError(
                f"Copilot bridge directory not found: {bridge_dir}\n"
                f"Please run: cd {bridge_dir} && npm install"
            )

        # Check if node_modules exists
        node_modules = bridge_dir / "node_modules"
        if not node_modules.exists():
            logger.warning(
                f"Bridge dependencies not installed. Running npm install in {bridge_dir}"
            )
            subprocess.run(
                ["npm", "install"],
                cwd=bridge_dir,
                check=True,
                capture_output=True,
            )

        logger.info(f"Starting bridge server from {bridge_dir}")
        self.bridge_process = subprocess.Popen(
            ["node", "server.js"],
            cwd=bridge_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "COPILOT_BRIDGE_PORT": self.bridge_url.split(":")[-1]},
        )

        # Wait for server to start
        max_wait = 10
        for i in range(max_wait):
            try:
                response = self.session.get(f"{self.bridge_url}/health", timeout=1)
                if response.status_code == 200:
                    logger.info("Bridge server started successfully")
                    return
            except requests.exceptions.ConnectionError:
                time.sleep(1)

        raise RuntimeError(
            f"Failed to start bridge server after {max_wait} seconds"
        )

    async def create_session(self) -> str:
        """
        Create a new session with the bridge server.

        Returns:
            Session ID
        """
        self._ensure_bridge_running()

        response = self.session.post(
            f"{self.bridge_url}/session/create",
            json={
                "model": self.model,
                "workingDirectory": self.working_directory,
                "availableTools": self.available_tools,
                "mcpServers": self.mcp_servers,
                "systemMessage": self.system_message,
            },
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()
        self.session_id = data["sessionId"]
        logger.info(f"Created session: {self.session_id}")
        return self.session_id

    async def query(self, prompt: str) -> None:
        """
        Send a query to the session.

        Args:
            prompt: The prompt to send
        """
        if not self.session_id:
            await self.create_session()

        logger.debug(f"Sending query to session {self.session_id}")
        # Store the prompt for retrieval by receive_response
        self._current_prompt = prompt

    async def receive_response(self) -> AsyncIterator[Any]:
        """
        Receive streaming response from the session.

        Yields:
            Message objects with events from the assistant
        """
        if not self.session_id:
            raise RuntimeError("No active session. Call query() first.")

        # Send the query with streaming response
        response = self.session.post(
            f"{self.bridge_url}/session/{self.session_id}/query",
            json={"prompt": self._current_prompt},
            stream=True,
            timeout=(5, None),  # 5s connect, infinite read
        )
        response.raise_for_status()

        # Parse SSE stream
        for line in response.iter_lines():
            if line:
                line_str = line.decode("utf-8")
                if line_str.startswith("data: "):
                    data_str = line_str[6:]  # Remove "data: " prefix
                    try:
                        event_data = json.loads(data_str)
                        event_type = event_data.get("type")

                        if event_type == "done":
                            break
                        elif event_type == "error":
                            logger.error(f"Error from bridge: {event_data.get('error')}")
                            break
                        elif event_type != "connected":
                            # Convert Copilot SDK events to Claude-like messages
                            yield self._convert_event(event_data)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse SSE data: {e}")

    def _convert_event(self, event: dict) -> Any:
        """
        Convert Copilot SDK event to Claude SDK message format.

        Args:
            event: Event from Copilot SDK

        Returns:
            Message object compatible with existing code
        """
        event_type = event.get("type")

        # Create a simple message object that mimics Claude SDK structure
        class Message:
            def __init__(self, event_data):
                self.type = "message"
                self.content = []
                self._raw_event = event_data

                # Map Copilot events to Claude-like structure
                if event_type == "assistant.message":
                    self.content.append(TextBlock(event_data.get("data", {}).get("message", "")))
                elif event_type == "assistant.tool_use":
                    tool_data = event_data.get("data", {})
                    self.content.append(
                        ToolUseBlock(
                            name=tool_data.get("tool"),
                            input=tool_data.get("input", {}),
                        )
                    )
                elif event_type == "tool.result":
                    # Tool results are sent as UserMessage
                    pass

        class TextBlock:
            def __init__(self, text):
                self.text = text

        class ToolUseBlock:
            def __init__(self, name, input):
                self.name = name
                self.input = input

        class UserMessage:
            def __init__(self, content):
                self.content = [ToolResultBlock(content)]

        class ToolResultBlock:
            def __init__(self, content):
                self.content = content
                self.is_error = False

        # Return appropriate message type
        if event_type in ("assistant.message", "assistant.tool_use"):
            return Message(event)
        elif event_type == "tool.result":
            return UserMessage(event.get("data", {}).get("result", ""))
        else:
            # Unknown event type, wrap it
            return Message(event)

    async def abort(self) -> None:
        """Abort the current session."""
        if not self.session_id:
            return

        try:
            response = self.session.post(
                f"{self.bridge_url}/session/{self.session_id}/abort",
                timeout=5,
            )
            response.raise_for_status()
            logger.info(f"Aborted session {self.session_id}")
        except Exception as e:
            logger.error(f"Failed to abort session: {e}")

    async def close(self) -> None:
        """Close the session and cleanup."""
        if self.session_id:
            try:
                response = self.session.delete(
                    f"{self.bridge_url}/session/{self.session_id}",
                    timeout=5,
                )
                response.raise_for_status()
                logger.info(f"Closed session {self.session_id}")
            except Exception as e:
                logger.error(f"Failed to close session: {e}")
            finally:
                self.session_id = None

        # Stop bridge process if we started it
        if self.bridge_process:
            self.bridge_process.terminate()
            try:
                self.bridge_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.bridge_process.kill()
            self.bridge_process = None

    def __del__(self):
        """Cleanup on deletion."""
        if self.session_id:
            # Use asyncio if available, otherwise just log
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self.close())
                else:
                    loop.run_until_complete(self.close())
            except Exception:
                logger.debug("Could not close session in __del__")
