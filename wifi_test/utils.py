"""Utility functions for wifi-test."""

import shutil
import subprocess
from typing import Optional


def run_cmd(
    cmd: list[str],
    timeout: int = 10,
) -> subprocess.CompletedProcess | None:
    """Run a subprocess command with standard error handling.

    Args:
        cmd: Command and arguments to run
        timeout: Timeout in seconds (default: 10)

    Returns:
        CompletedProcess if successful, None if an error occurred
    """
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (
        subprocess.TimeoutExpired,
        subprocess.CalledProcessError,
        FileNotFoundError,
    ):
        return None


def check_command_available(
    command: str, display_name: Optional[str] = None
) -> tuple[bool, str]:
    """Check if a command is available in the system PATH.

    Args:
        command: Command name to check (e.g., 'iperf3', 'speedtest')
        display_name: Human-readable name for error messages (defaults to command)

    Returns:
        Tuple of (is_available, error_message)
        If available: (True, "")
        If not available: (False, error_message)
    """
    display_name = display_name or command

    # Check if command exists in PATH
    if not shutil.which(command):
        error_msg = (
            f"✗ {display_name} not found in PATH.\n"
            f"  Please install {display_name} and ensure it's in your PATH."
        )
        return (False, error_msg)

    # Try to run with --version to verify it actually works
    result = run_cmd([command, "--version"], timeout=5)
    if result is None:
        error_msg = f"✗ {display_name} command failed. Please check your installation."
        return (False, error_msg)
    return (True, "")


def verify_speedtest_available() -> tuple[bool, str]:
    """Verify that speedtest CLI is installed and available.

    Returns:
        Tuple of (is_available, error_message)
    """
    return check_command_available("speedtest", "Ookla Speedtest CLI")


def verify_iperf3_available() -> tuple[bool, str]:
    """Verify that iperf3 is installed and available.

    Returns:
        Tuple of (is_available, error_message)
    """
    return check_command_available("iperf3", "iperf3")


def get_missing_dependencies(tools: list[str]) -> list[str]:
    """Check which tools from a list are missing.

    Args:
        tools: List of tool names ('speedtest' or 'iperf3')

    Returns:
        List of missing tool names
    """
    missing = []

    for tool in tools:
        if tool == "speedtest":
            available, _ = verify_speedtest_available()
            if not available:
                missing.append("Ookla Speedtest CLI")
        elif tool == "iperf3":
            available, _ = verify_iperf3_available()
            if not available:
                missing.append("iperf3")

    return missing
