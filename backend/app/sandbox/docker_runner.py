
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path


DEFAULT_IMAGE = "python:3.12-slim"

IGNORED_DIRS = {
    ".git",
    "venv",
    ".venv",
    "__pycache__",
    "node_modules",
    ".next",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
}

MAX_OUTPUT_CHARS = 20_000
CLEANUP_TIMEOUT = 8


class DockerSandboxError(Exception):
    """Raised when Forge cannot start or manage a Docker sandbox."""


def _output(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _run_command(command: list[str], timeout: int) -> dict:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )

        return {
            "exit_code": result.returncode,
            "stdout": result.stdout[-MAX_OUTPUT_CHARS:],
            "stderr": result.stderr[-MAX_OUTPUT_CHARS:],
            "timed_out": False,
        }

    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": None,
            "stdout": _output(exc.stdout)[-MAX_OUTPUT_CHARS:],
            "stderr": _output(exc.stderr)[-MAX_OUTPUT_CHARS:],
            "timed_out": True,
        }


def check_docker() -> None:
    """Check whether Docker is installed and the daemon is reachable."""
    if shutil.which("docker") is None:
        raise DockerSandboxError("Docker CLI was not found.")

    result = _run_command(["docker", "info"], 15)

    if result["timed_out"]:
        raise DockerSandboxError("Docker did not respond in time.")

    if result["exit_code"] != 0:
        raise DockerSandboxError(
            "Docker is unavailable. Make sure Docker Desktop is running."
        )


def prepare_workspace(repo_path: str | Path, destination: Path) -> None:
    """
    Copy the repository into a temporary workspace.

    Forge executes against this copy, never the original repository.
    """
    source = Path(repo_path).resolve()

    if not source.is_dir():
        raise ValueError(f"Repository directory does not exist: {source}")

    def ignore_entries(directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in IGNORED_DIRS
        }

    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=ignore_entries,
        symlinks=False,
    )


def _cleanup_container(container_name: str) -> str | None:
    result = _run_command(
        ["docker", "rm", "-f", container_name],
        CLEANUP_TIMEOUT,
    )

    if result["timed_out"]:
        return "Docker container cleanup timed out."

    if result["exit_code"] != 0:
        message = result["stderr"].strip()
        if "No such container" in message:
            return None
        return message or "Docker container cleanup failed."

    return None


def run_in_sandbox(
    repo_path: str | Path,
    command: list[str],
    image: str = DEFAULT_IMAGE,
    timeout_seconds: int = 120,
    memory_limit: str = "1g",
    cpu_limit: str = "1.0",
) -> dict:
    """
    Execute a command inside an isolated Docker container.

    The repository is copied into a temporary workspace first.
    The original repository is never mounted into the container.
    """

    if not isinstance(command, list) or not command:
        raise ValueError("Command must be a non-empty list of strings.")

    if not all(isinstance(part, str) and part for part in command):
        raise ValueError("Every command argument must be a non-empty string.")

    if any("\x00" in part for part in command):
        raise ValueError("Command arguments cannot contain null bytes.")

    if not 1 <= timeout_seconds <= 600:
        raise ValueError("Timeout must be between 1 and 600 seconds.")

    check_docker()

    container_name = f"forge-run-{uuid.uuid4().hex[:12]}"
    started_at = time.perf_counter()
    cleanup_warning = None

    try:
        with tempfile.TemporaryDirectory(
            prefix="forge-sandbox-"
        ) as temp_dir:

            sandbox_repo = Path(temp_dir) / "repository"
            prepare_workspace(repo_path, sandbox_repo)

            docker_command = [
                "docker",
                "run",
                "--name", container_name,
                "--rm",
                "--network", "none",
                "--cpus", cpu_limit,
                "--memory", memory_limit,
                "--memory-swap", memory_limit,
                "--pids-limit", "96",
                "--security-opt", "no-new-privileges",
                "--cap-drop", "ALL",
                "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
                "--mount",
                f"type=bind,source={sandbox_repo},target=/workspace",
                "--workdir", "/workspace",
                "--env", "PYTHONDONTWRITEBYTECODE=1",
                "--env", "PYTEST_ADDOPTS=-p no:cacheprovider",
                image,
                *command,
            ]

            result = _run_command(
                docker_command,
                timeout_seconds,
            )

            duration = round(time.perf_counter() - started_at, 2)

            if result["timed_out"]:
                status = "timeout"
                error = f"Execution exceeded {timeout_seconds} seconds."
            else:
                status = (
                    "passed" if result["exit_code"] == 0 else "failed"
                )
                error = None

            return {
                "status": status,
                "exit_code": result["exit_code"],
                "command": command,
                "image": image,
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "duration_seconds": duration,
                "execution_performed": True,
                "error": error,
                "sandbox": {
                    "network": "disabled",
                    "memory_limit": memory_limit,
                    "cpu_limit": cpu_limit,
                    "pids_limit": 96,
                    "original_repository_modified": False,
                },
            }

    finally:
        # docker run --rm normally removes the container itself.
        # This is a bounded fallback for interrupted/timed-out runs.
        cleanup_warning = _cleanup_container(container_name)
