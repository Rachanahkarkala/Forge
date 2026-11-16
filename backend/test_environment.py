
from pathlib import Path
from pprint import pprint

from app.sandbox.environment_manager import (
    detect_python_environment,
    build_python_install_plan,
)

REPO = (
    Path(__file__).resolve().parents[1]
    / "workspaces"
    / "fastapi-fastapi-0255d187"
)

print("\n========== PYTHON ENVIRONMENT ==========")

environment = detect_python_environment(REPO)

print("Project:", environment["project_name"])
print("Python requirement:", environment["python_requirement"])
print("Build backend:", environment["build_backend"])
print("Core dependencies:", len(environment["core_dependencies"]))
print("Optional groups:", environment["optional_dependency_groups"])
print("Test dependencies:", len(environment["test_dependencies"]))
print("Pytest timeout plugin required:",
      environment["pytest_timeout_plugin_required"])

print("\n========== TEST DEPENDENCIES ==========")
pprint(environment["test_dependencies"])

print("\n========== INSTALLATION PLAN ==========")
pprint(build_python_install_plan(REPO)["install_commands"])
