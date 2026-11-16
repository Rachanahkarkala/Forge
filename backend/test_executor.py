
import tempfile
from pathlib import Path

from app.sandbox.test_executor import (
    detect_python_test_framework,
    execute_python_tests,
)


with tempfile.TemporaryDirectory(prefix="forge-test-") as temp_dir:
    repo = Path(temp_dir)

    (repo / "test_math.py").write_text(
        """
def add(a, b):
    return a + b


def test_add():
    assert add(2, 3) == 5


def test_add_negative_numbers():
    assert add(-2, -3) == -5
""",
        encoding="utf-8",
    )

    print("\n========== FRAMEWORK DETECTION ==========")
    print(detect_python_test_framework(repo))

    print("\n========== TEST EXECUTION ==========")

    result = execute_python_tests(repo)

    print("Status:", result["status"])
    print("Framework:", result["framework"])
    print("Selected files:", result["selected_test_files"])
    print("Exit code:", result["exit_code"])
    print("Output:\n", result["stdout"])
    print("Execution performed:", result["test_execution_performed"])
    print("====================================")
