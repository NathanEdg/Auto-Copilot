"""
Authentication helpers for Auto Copilot.

Provides centralized authentication token resolution with fallback support
for multiple environment variables, and SDK environment variable passthrough
for custom API endpoints.
"""

import json
import os
import platform
import subprocess

# Priority order for auth token resolution
# NOTE: We use GitHub authentication for Copilot access
AUTH_TOKEN_ENV_VARS = [
    "GITHUB_TOKEN",  # GitHub Personal Access Token or gh CLI token
    "GH_TOKEN",  # Alternative GitHub token env var
]

# Environment variables to pass through to SDK subprocess
SDK_ENV_VARS = [
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "NO_PROXY",
    "DISABLE_TELEMETRY",
    "API_TIMEOUT_MS",
]


def get_token_from_gh_cli() -> str | None:
    """
    Get authentication token from GitHub CLI.

    Attempts to retrieve GitHub token from gh CLI auth status.
    Works on all platforms where gh CLI is installed.

    Returns:
        Token string if found, None otherwise
    """
    try:
        # Query gh CLI for auth token
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            return None

        token = result.stdout.strip()
        if not token:
            return None

        # Basic validation - GitHub tokens start with various prefixes
        # (ghp_, gho_, ghu_, ghs_, ghr_, github_pat_)
        valid_prefixes = ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_")
        if not any(token.startswith(prefix) for prefix in valid_prefixes):
            return None

        return token

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        # Silently fail - this is a fallback mechanism
        return None


def get_auth_token() -> str | None:
    """
    Get authentication token from environment variables or macOS Keychain.

def get_auth_token() -> str | None:
    """
    Get authentication token from environment variables or gh CLI.

    Checks multiple sources in priority order:
    1. GITHUB_TOKEN (env var)
    2. GH_TOKEN (alternative env var)
    3. gh CLI (via `gh auth token`)

    Returns:
        Token string if found, None otherwise
    """
    # First check environment variables
    for var in AUTH_TOKEN_ENV_VARS:
        token = os.environ.get(var)
        if token:
            return token

    # Fallback to gh CLI
    return get_token_from_gh_cli()


def get_auth_token_source() -> str | None:
    """Get the name of the source that provided the auth token."""
    # Check environment variables first
    for var in AUTH_TOKEN_ENV_VARS:
        if os.environ.get(var):
            return var

    # Check if token came from gh CLI
    if get_token_from_gh_cli():
        return "gh CLI"

    return None


def require_auth_token() -> str:
    """
    Get authentication token or raise ValueError.

    Raises:
        ValueError: If no auth token is found in any supported source
    """
    token = get_auth_token()
    if not token:
        error_msg = (
            "No GitHub token found.\n\n"
            "Auto Copilot requires GitHub authentication to use Copilot.\n\n"
            "To authenticate:\n"
            "  1. Run: gh auth login\n"
            "  2. Or set GITHUB_TOKEN in your .env file\n"
            "  3. Token will be detected automatically from gh CLI or env var"
        )
        raise ValueError(error_msg)
    return token


def get_sdk_env_vars() -> dict[str, str]:
    """
    Get environment variables to pass to SDK.

    Collects relevant env vars (GITHUB_TOKEN, etc.) that should
    be passed through to the bridge server or SDK subprocess.

    Returns:
        Dict of env var name -> value for non-empty vars
    """
    env = {}
    for var in SDK_ENV_VARS:
        value = os.environ.get(var)
        if value:
            env[var] = value
    return env


def ensure_github_token() -> None:
    """
    Ensure GITHUB_TOKEN is set (for bridge compatibility).

    If not set but other auth tokens are available, copies the value
    to GITHUB_TOKEN so the bridge server can use it.
    """
    if os.environ.get("GITHUB_TOKEN"):
        return

    token = get_auth_token()
    if token:
        os.environ["GITHUB_TOKEN"] = token
