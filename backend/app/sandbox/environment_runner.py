
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


def _docker_exec(
    container_name: str,
    command: list[str],
    timeout: int,
) -> dict:
    """Execute a command inside an existing container."""

    result = subprocess.run(
        ["docker", "exec", container_name, *command],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        check=False,
    )

    return {
        "exit_code": result.returncode,
        "stdout": result.stdout[-20000:],
        "stderr": result.stderr[-20000:],
    }


def prepare_and_run_python_tests(
    repo_path: str | Path,
    test_paths: list[str],
    image: str = DEFAULT_IMAGE,
    install_timeout: int = 600,
    test_timeout: int = 180,
) -> dict:
    """
    Prepare Python dependencies with temporary network access,
    disconnect networking, then execute tests in the same container.
    """

    source = Path(repo_path).resolve()

    if not source.is_dir():
        raise ValueError("Repository directory does not exist.")

    if not test_paths:
        raise ValueError("At least one test path is required.")

    for relative_path in test_paths:
        candidate = Path(relative_path)

        if (
            candidate.is_absolute()
            or ".." in candidate.parts
            or not (source / candidate).is_file()
        ):
            raise ValueError(f"Invalid test path: {relative_path}")

    plan = build_python_install_plan(source)

    container_name = f"forge-env-{uuid.uuid4().hex[:12]}"
    started_at = time.perf_counter()

    install_results = []
    test_result = None
    network_disconnected = False
    status = "installation_error"
    error = None

    check_docker()

    try:
        with tempfile.TemporaryDirectory(
            prefix="forge-environment-"
        ) as temp_dir:

            sandbox_repo = Path(temp_dir) / "repository"
            prepare_workspace(source, sandbox_repo)

            requirements = plan["test_requirements"]

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
                "--cpus", "1.0",
                "--memory", "2g",
                "--pids-limit", "128",
                "--security-opt", "no-new-privileges",
                "--cap-drop", "ALL",
                "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
                "--tmpfs", "/opt/forge-venv:rw,exec,nosuid,size=2g",
                "--mount",
                f"type=bind,source={sandbox_repo},target=/workspace",
                "--workdir", "/workspace",
                image,
                "sleep", "900",
            ]

            created = subprocess.run(
                create_command,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            if created.returncode != 0:
                raise RuntimeError(
                    f"Container creation failed: {created.stderr}"
                )

            started = subprocess.run(
                ["docker", "start", container_name],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            if started.returncode != 0:
                raise RuntimeError(
                    f"Container start failed: {started.stderr}"
                )

            # Create a writable virtual environment on tmpfs.
            venv_result = _docker_exec(
                container_name,
                ["python", "-m", "venv", "/opt/forge-venv"],
                60,
            )

            if venv_result["exit_code"] != 0:
                raise RuntimeError(
                    f"Virtual environment creation failed: "
                    f"{venv_result['stderr']}"
                )

            python = "/opt/forge-venv/bin/python"

            # Install project and test dependencies.
            for command in plan["install_commands"]:
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

                if result["exit_code"] != 0:
                    status = "installation_error"
                    error = "Dependency installation failed."
                    break

            else:
                # Disconnect the container before running untrusted tests.
                disconnected = subprocess.run(
                    [
                        "docker",
                        "network",
                        "disconnect",
                        "bridge",
                        container_name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )

                if disconnected.returncode != 0:
                    raise RuntimeError(
                        "Could not disconnect container networking: "
                        + disconnected.stderr
                    )

                network_disconnected = True

                test_command = [
                    python,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    *test_paths,
                ]

                test_result = _docker_exec(
                    container_name,
                    test_command,
                    test_timeout,
                )

                status = (
                    "passed"
                    if test_result["exit_code"] == 0
                    else "failed"
                )

    except subprocess.TimeoutExpired:
        status = "timeout"
        error = "Dependency preparation or test execution timed out."

    except Exception as exc:
        status = "execution_error"
        error = str(exc)

    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    return {
        "status": status,
        "project": plan["environment"]["project_name"],
        "framework": "pytest",
        "selected_test_files": test_paths,
        "dependency_installation": {
            "status": (
                "passed"
                if install_results
                and all(item["exit_code"] == 0 for item in install_results)
                else "failed"
            ),
            "steps": install_results,
        },
        "test_result": test_result,
        "network_disabled_during_tests": network_disconnected,
        "original_repository_modified": False,
        "execution_performed": test_result is not None,
        "duration_seconds": round(
            time.perf_counter() - started_at, 2
        ),
        "error": error,
    }
