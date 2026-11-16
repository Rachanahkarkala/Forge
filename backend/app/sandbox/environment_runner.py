
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from app.sandbox.docker_runner import (
    check_docker,
    prepare_workspace,
    DEFAULT_IMAGE,
)
from app.sandbox.environment_manager import build_python_install_plan


MAX_TEST_FILES = 3
MAX_OUTPUT_CHARS = 20_000
CONTAINER_MEMORY = "1536m"
CONTAINER_CPUS = "1.5"
CONTAINER_PIDS = "96"
CLEANUP_TIMEOUT = 8


def _output(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _run_command(command: list[str], timeout: int) -> dict:
    """Run a host-side command with a strict timeout."""
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


def _docker_exec(
    container_name: str,
    command: list[str],
    timeout: int,
) -> dict:
    """Execute a command inside an existing container."""
    result = _run_command(
        ["docker", "exec", container_name, *command],
        timeout,
    )

    return result


def _cleanup_container(container_name: str) -> str | None:
    """
    Attempt cleanup without allowing cleanup errors to replace
    the actual installation or test result.
    """
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


def _validate_test_paths(
    source: Path,
    test_paths: list[str],
) -> tuple[list[str], list[str]]:
    if not isinstance(test_paths, list) or not test_paths:
        raise ValueError("At least one test path is required.")

    valid_paths = []

    for relative_path in test_paths:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError("Test paths must be non-empty strings.")

        candidate = Path(relative_path)

        if (
            candidate.is_absolute()
            or ".." in candidate.parts
            or not (source / candidate).is_file()
        ):
            raise ValueError(f"Invalid test path: {relative_path}")

        valid_paths.append(candidate.as_posix())

    selected = valid_paths[:MAX_TEST_FILES]
    omitted = valid_paths[MAX_TEST_FILES:]

    return selected, omitted


def prepare_and_run_python_tests(
    repo_path: str | Path,
    test_paths: list[str],
    image: str = DEFAULT_IMAGE,
    install_timeout: int = 240,
    test_timeout: int = 150,
) -> dict:
    """
    Prepare Python dependencies with temporary network access,
    disconnect networking, then execute a bounded test selection.
    """

    source = Path(repo_path).resolve()

    if not source.is_dir():
        raise ValueError("Repository directory does not exist.")

    selected_tests, omitted_tests = _validate_test_paths(
        source,
        test_paths,
    )

    plan = build_python_install_plan(source)

    container_name = f"forge-env-{uuid.uuid4().hex[:12]}"
    started_at = time.perf_counter()

    install_results = []
    test_result = None
    network_disconnected = False
    execution_attempted = False
    status = "installation_error"
    error = None
    cleanup_warning = None
    dependency_status = "not_started"

    check_docker()

    try:
        with tempfile.TemporaryDirectory(
            prefix="forge-environment-"
        ) as temp_dir:

            sandbox_repo = Path(temp_dir) / "repository"
            prepare_workspace(source, sandbox_repo)

            requirements = plan.get("test_requirements", [])

            if requirements:
                (
                    sandbox_repo / ".forge-test-requirements.txt"
                ).write_text(
                    "\n".join(requirements) + "\n",
                    encoding="utf-8",
                )

            create_command = [
                "docker", "create",
                "--name", container_name,
                "--network", "bridge",
                "--cpus", CONTAINER_CPUS,
                "--memory", CONTAINER_MEMORY,
                "--memory-swap", CONTAINER_MEMORY,
                "--pids-limit", CONTAINER_PIDS,
                "--shm-size", "64m",
                "--security-opt", "no-new-privileges",
                "--cap-drop", "ALL",
                "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
                "--tmpfs", "/opt/forge-venv:rw,exec,nosuid,size=768m",
                "--mount",
                f"type=bind,source={sandbox_repo},target=/workspace",
                "--workdir", "/workspace",
                "--env", "PYTHONDONTWRITEBYTECODE=1",
                image,
                "sleep", "900",
            ]

            created = _run_command(create_command, 30)

            if created["timed_out"]:
                raise TimeoutError("Docker container creation timed out.")

            if created["exit_code"] != 0:
                raise RuntimeError(
                    "Container creation failed: "
                    + created["stderr"]
                )

            started = _run_command(
                ["docker", "start", container_name],
                30,
            )

            if started["timed_out"]:
                raise TimeoutError("Docker container startup timed out.")

            if started["exit_code"] != 0:
                raise RuntimeError(
                    "Container start failed: " + started["stderr"]
                )

            # Keep the venv outside /tmp because /tmp is mounted noexec.
            venv_result = _docker_exec(
                container_name,
                ["python", "-m", "venv", "/opt/forge-venv"],
                45,
            )

            if venv_result["timed_out"]:
                raise TimeoutError("Virtual environment creation timed out.")

            if venv_result["exit_code"] != 0:
                raise RuntimeError(
                    "Virtual environment creation failed: "
                    + venv_result["stderr"]
                )

            python = "/opt/forge-venv/bin/python"

            # Install dependencies while temporary network access is available.
            for command in plan.get("install_commands", []):
                prepared_command = [
                    python if part == "python" else part
                    for part in command
                ]

                result = _docker_exec(
                    container_name,
                    prepared_command,
                    install_timeout,
                )

                install_results.append({
                    "command": prepared_command,
                    **result,
                })

                if result["timed_out"]:
                    dependency_status = "timeout"
                    status = "timeout"
                    error = "Dependency installation timed out."
                    break

                if result["exit_code"] != 0:
                    dependency_status = "failed"
                    status = "installation_error"
                    error = "Dependency installation failed."
                    break

            else:
                dependency_status = "passed"

                # Tests must not have network access.
                disconnected = _run_command(
                    [
                        "docker",
                        "network",
                        "disconnect",
                        "bridge",
                        container_name,
                    ],
                    20,
                )

                if disconnected["timed_out"]:
                    raise TimeoutError(
                        "Disconnecting Docker networking timed out."
                    )

                if disconnected["exit_code"] != 0:
                    raise RuntimeError(
                        "Could not disconnect container networking: "
                        + disconnected["stderr"]
                    )

                network_disconnected = True

                test_command = [
                    python,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    *selected_tests,
                ]

                execution_attempted = True

                test_result = _docker_exec(
                    container_name,
                    test_command,
                    test_timeout,
                )

                if test_result["timed_out"]:
                    status = "timeout"
                    error = f"Test execution exceeded {test_timeout} seconds."

                elif test_result["exit_code"] == 0:
                    status = "passed"

                else:
                    status = "failed"

    except TimeoutError as exc:
        status = "timeout"
        error = str(exc)

    except Exception as exc:
        status = "execution_error"
        error = str(exc)

    finally:
        cleanup_warning = _cleanup_container(container_name)

    duration = round(time.perf_counter() - started_at, 2)

    return {
        "status": status,
        "project": plan.get("environment", {}).get(
            "project_name", source.name
        ),
        "framework": "pytest",
        "selected_test_files": selected_tests,
        "omitted_test_files": omitted_tests,
        "test_file_limit": MAX_TEST_FILES,
        "dependency_installation": {
            "status": dependency_status,
            "steps": install_results,
        },
        "test_result": test_result,
        "network_disabled_during_tests": network_disconnected,
        "original_repository_modified": False,
        "execution_attempted": execution_attempted,
        "execution_performed": execution_attempted,
        "duration_seconds": duration,
        "error": error,
        "cleanup_warning": cleanup_warning,
        "sandbox": {
            "memory_limit": CONTAINER_MEMORY,
            "cpu_limit": CONTAINER_CPUS,
            "pids_limit": int(CONTAINER_PIDS),
        },
    }
