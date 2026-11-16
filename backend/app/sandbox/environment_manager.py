
import tomllib
from pathlib import Path


def load_pyproject(repo_path: str | Path) -> dict:
    """Read a project's pyproject.toml safely."""

    manifest = Path(repo_path) / "pyproject.toml"

    if not manifest.is_file():
        raise FileNotFoundError("pyproject.toml was not found.")

    with manifest.open("rb") as file:
        return tomllib.load(file)


def resolve_dependency_group(
    groups: dict,
    group_name: str,
    visited: set | None = None,
) -> list[str]:
    """Resolve PEP 735 dependency groups and nested includes."""

    if visited is None:
        visited = set()

    if group_name in visited:
        raise ValueError(
            f"Circular dependency group reference: {group_name}"
        )

    if group_name not in groups:
        raise ValueError(
            f"Dependency group '{group_name}' does not exist."
        )

    visited.add(group_name)
    dependencies = []

    for item in groups[group_name]:
        if isinstance(item, str):
            dependencies.append(item)

        elif isinstance(item, dict) and "include-group" in item:
            included = resolve_dependency_group(
                groups,
                item["include-group"],
                visited.copy(),
            )
            dependencies.extend(included)

    # Preserve order while removing duplicates.
    return list(dict.fromkeys(dependencies))


def detect_python_environment(repo_path: str | Path) -> dict:
    """Detect Python dependencies and testing configuration."""

    data = load_pyproject(repo_path)

    project = data.get("project", {})
    build_system = data.get("build-system", {})
    optional_dependencies = project.get(
        "optional-dependencies", {}
    )
    dependency_groups = data.get("dependency-groups", {})
    pytest_config = data.get("tool", {}).get("pytest", {})

    groups = {}

    if "tests" in dependency_groups:
        groups["tests"] = resolve_dependency_group(
            dependency_groups,
            "tests",
        )

    return {
        "language": "python",
        "manifest": "pyproject.toml",
        "project_name": project.get("name"),
        "python_requirement": project.get("requires-python"),
        "build_backend": build_system.get("build-backend"),
        "core_dependencies": project.get("dependencies", []),
        "optional_dependency_groups": list(
            optional_dependencies.keys()
        ),
        "test_dependency_group": "tests" if "tests" in groups else None,
        "test_dependencies": groups.get("tests", []),
        "pytest_configuration": pytest_config,
        "pytest_timeout_plugin_required": (
            "timeout" in pytest_config
        ),
    }



def build_python_install_plan(repo_path: str | Path) -> dict:
    """
    Build a Python installation plan without modifying the repository.
    The requirements file will be created in the disposable sandbox.
    """

    environment = detect_python_environment(repo_path)

    install_commands = [
        [
            "python",
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--editable",
            ".[standard]",
        ]
    ]

    if environment["test_dependencies"]:
        install_commands.append([
            "python",
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "-r",
            ".forge-test-requirements.txt",
        ])

    return {
        "environment": environment,
        "install_commands": install_commands,
        "test_requirements": environment["test_dependencies"],
    }
