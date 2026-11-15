
from pathlib import Path

from app.repositories.search import search_relevant_files


BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR.parent / "workspaces"

repositories = {
    "FastAPI": "fastapi-fastapi-b1d4d6e6",
    "Flask": "flask-pallets",
    "Django": "django-django",
    "React": "react-facebook",
    "Requests": "requests-psf",
}

issue_text = "Add tests for request validation and edge cases"

print("\n========== FORGE MULTI-REPOSITORY TEST ==========\n")

for name, folder in repositories.items():

    repo_path = WORKSPACE_DIR / folder

    print(f"Testing {name}...")

    if not repo_path.is_dir():
        print("  ERROR: Repository folder not found\n")
        continue

    try:
        result = search_relevant_files(
            repo_path=repo_path,
            issue_text=issue_text,
            top_k=3,
        )

        print(f"  Files scanned: {result['files_scanned']}")
        print("  Top results:")

        for item in result["results"]:
            print(
                f"    - {item['file']} "
                f"(score: {item['score']})"
            )

        print("  STATUS: SUCCESS\n")

    except Exception as error:
        print(f"  STATUS: FAILED")
        print(f"  ERROR: {error}\n")

print("========== TEST COMPLETE ==========")
