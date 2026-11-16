
from pathlib import Path

from app.sandbox.docker_runner import run_in_sandbox


BACKEND_DIR = Path(__file__).resolve().parent

result = run_in_sandbox(
    repo_path=BACKEND_DIR,
    command=[
        "python",
        "-c",
        "print('FORGE SANDBOX WORKING!')",
    ],
)

print("\n========== FORGE SANDBOX TEST ==========")
print("Status:", result["status"])
print("Exit code:", result["exit_code"])
print("Output:", result["stdout"])
print("Duration:", result["duration_seconds"], "seconds")
print("Original repository modified:", result["sandbox"]["original_repository_modified"])
print("========================================")
