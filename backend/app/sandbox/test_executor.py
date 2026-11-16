
from pathlib import Path
from typing import Optional

from app.sandbox.docker_runner import run_in_sandbox


TEST_IMAGE = "forge-python-test:latest"

IGNORED_DIRS = {
    ".git",
    "venv",
    ".venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    "dist",
    "build",
}


def discover_python_tests(repo_path: str | Path) -> list[str]:
    """Find Python test files without scanning generated directories."""
    root = Path(repo_path).resolve()
    discovered = []

    for path in root.rglob("*.py"):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue

        if path.is_symlink() or not path.is_file():
            continue

        name = path.name.lower()

        if (
            name.startswith("test_")
            or name.endswith("_test.py")
            or "tests" in path.relative_to(root).parts
        ):
            discovered.append(path.relative_to(root).as_posix())

    return sorted(set(discovered))


def detect_python_test_framework(repo_path: str | Path) -> dict:
    """Identify likely Python test frameworks from repository files."""
    root = Path(repo_path).resolve()

    if not root.is_dir():
        raise ValueError(f"Repository does not exist: {root}")

    test_files = discover_python_tests(root)

    config_files = [
        "pytest.ini",
        "pyproject.toml",
        "setup.cfg",
        "tox.ini",
    ]

    existing_configs = [
        name for name in config_files if (root / name).is_file()
    ]

    has_pytest_config = "pytest.ini" in existing_configs

    if (root / "pyproject.toml").is_file():
        content = (root / "pyproject.toml").read_text(
            encoding="utf-8", errors="replace"
        ).lower()
        has_pytest_config = has_pytest_config or "[tool.pytest" in content

    if (root / "setup.cfg").is_file():
        content = (root / "setup.cfg").read_text(
            encoding="utf-8", errors="replace"
        ).lower()
        has_pytest_config = has_pytest_config or "[tool:pytest]" in content

    if has_pytest_config or test_files:
        framework = "pytest"
        confidence = "likely"
    else:
        framework = "unittest"
        confidence = "possible"

    return {
        "language": "python",
        "framework": framework,
        "confidence": confidence,
        "test_files_found": len(test_files),
        "test_files": test_files,
        "configuration_files": existing_configs,
    }


def execute_python_tests(
    repo_path: str | Path,
    test_paths: Optional[list[str]] = None,
    timeout_seconds: int = 180,
) -> dict:
    """
    Discover and execute Python tests inside the Forge Docker sandbox.

    If test_paths are provided, only those repository-relative paths
    are selected. Otherwise, all discovered Python test files are used.
    """
    root = Path(repo_path).resolve()
    detection = detect_python_test_framework(root)

    discovered = detection["test_files"]

    if test_paths is not None:
        selected = []

        for relative_path in test_paths:
            candidate = Path(relative_path)

            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError(f"Invalid test path: {relative_path}")

            normalized = candidate.as_posix()

            if normalized not in discovered:
                raise ValueError(
                    f"Selected path is not a discovered Python test file: "
                    f"{relative_path}"
                )

            selected.append(normalized)

        selected = sorted(set(selected))
    else:
        selected = discovered

    if not selected:
        return {
            "status": "no_tests_found",
            "framework": detection["framework"],
            "test_files_discovered": len(discovered),
            "selected_test_files": [],
            "execution_performed": False,
            "message": "Forge did not find any Python test files to execute.",
        }

    command = [
        "python",
        "-m",
        "pytest",
        "-q",
        *selected,
    ]

    result = run_in_sandbox(
        repo_path=root,
        command=command,
        image=TEST_IMAGE,
        timeout_seconds=timeout_seconds,
    )

    return {
        **result,
        "framework": "pytest",
        "test_files_discovered": len(discovered),
        "selected_test_files": selected,
        "test_execution_performed": True,
        "verification_status": (
            "passed" if result["status"] == "passed"
            else "failed" if result["status"] == "failed"
            else result["status"]
        ),
    }
