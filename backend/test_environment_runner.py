
from pathlib import Path
from pprint import pprint

from app.sandbox.environment_runner import (
    prepare_and_run_python_tests,
)

REPO = (
    Path(__file__).resolve().parents[1]
    / "workspaces"
    / "fastapi-fastapi-0255d187"
)

result = prepare_and_run_python_tests(
    repo_path=REPO,
    test_paths=["tests/test_path.py"],
    install_timeout=600,
    test_timeout=180,
)

print("\n========== FORGE ENVIRONMENT RUNNER ==========")
pprint(result)
print("==============================================")
