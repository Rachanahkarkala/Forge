
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


class DockerSandboxError(Exception):
    """Raised when Forge cannot start or manage a Docker sandbox."""


def check_docker() -> None:
    """Check whether Docker is installed and the daemon is reachable."""
    if shutil.which("docker") is None:
        raise DockerSandboxError("Docker CLI was not found.")

    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DockerSandboxError("Docker did not respond in time.") from exc

    if result.returncode != 0:
        raise DockerSandboxError(
            "Docker is not available. Make sure Docker Desktop is running."
        )


def prepare_workspace(repo_path: str | Path, destination: Path) -> None:
    """
    Copy the repository into a temporary workspace.

    Forge runs code against this copy, not the original clone.
    """
    source = Path(repo_path).resolve()

    if not source.exists() or not source.is_dir():
        raise ValueError(f"Repository directory does not exist: {source}")

    def ignore_entries(directory: str, names: list[str]) -> set[str]:
        ignored = set()

        for name in names:
            if name in IGNORED_DIRS:
                ignored.add(name)

        return ignored

    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=ignore_entries,
        symlinks=False,
    )


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

    The repository is copied to a temporary directory first.
    The original repository is not mounted into the container.
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

    with tempfile.TemporaryDirectory(prefix="forge-sandbox-") as temp_dir:
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
            "--pids-limit", "128",
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
            "--mount",
            f"type=bind,source={sandbox_repo},target=/workspace",
            "--workdir", "/workspace",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--env", "PYTEST_ADDOPTS=-p no:cacheprovider",
            image,
            *command,
        ]

        try:
            result = subprocess.run(
                docker_command,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )

            duration = round(time.perf_counter() - started_at, 2)

            return {
                "status": "passed" if result.returncode == 0 else "failed",
                "exit_code": result.returncode,
                "command": command,
                "image": image,
                "stdout": result.stdout[-MAX_OUTPUT_CHARS:],
                "stderr": result.stderr[-MAX_OUTPUT_CHARS:],
                "duration_seconds": duration,
                "execution_performed": True,
                "sandbox": {
                    "network": "disabled",
                    "memory_limit": memory_limit,
                    "cpu_limit": cpu_limit,
                    "pids_limit": 128,
                    "original_repository_modified": False,
                },
            }

        except subprocess.TimeoutExpired as exc:
            duration = round(time.perf_counter() - started_at, 2)

            return {
                "status": "timeout",
                "exit_code": None,
                "command": command,
                "image": image,
                "stdout": str(exc.stdout or "")[-MAX_OUTPUT_CHARS:],
                "stderr": str(exc.stderr or "")[-MAX_OUTPUT_CHARS:],
                "duration_seconds": duration,
                "execution_performed": True,
                "error": f"Execution exceeded {timeout_seconds} seconds.",
            }

        finally:
            # Ensure a timed-out or interrupted container is cleaned up.
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
