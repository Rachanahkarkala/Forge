
import argparse
import json
from pathlib import Path
from collections import defaultdict

from app.repositories.search import search_relevant_files


BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = (BASE_DIR.parent / "workspaces").resolve()
DATASET_PATH = BASE_DIR / "evaluation_dataset.json"


def normalize_repository_name(repository: str) -> str:
    """Convert owner/repo into the local workspace prefix."""
    return repository.replace("/", "-").lower()


def validate_repository_path(path: Path) -> Path:
    """Ensure a repository is inside the workspace directory."""
    resolved = path.resolve()

    if resolved.parent != WORKSPACE_DIR:
        raise ValueError(
            f"Repository is outside the workspace: {resolved}"
        )

    if not resolved.is_dir():
        raise FileNotFoundError(
            f"Repository not found: {resolved}"
        )

    return resolved


def resolve_repository(repository: str, repo_maps: dict) -> Path:
    """
    Resolve a GitHub owner/repo to its local workspace folder.

    Resolution order:
    1. Explicit --repo-map
    2. Exact folder name
    3. Unique prefix match
    """

    if repository in repo_maps:
        return validate_repository_path(
            WORKSPACE_DIR / repo_maps[repository]
        )

    prefix = normalize_repository_name(repository)

    if not WORKSPACE_DIR.exists():
        raise FileNotFoundError(
            f"Workspace directory not found: {WORKSPACE_DIR}"
        )

    exact_match = WORKSPACE_DIR / prefix

    if exact_match.is_dir():
        return validate_repository_path(exact_match)

    candidates = [
        path for path in WORKSPACE_DIR.iterdir()
        if path.is_dir() and path.name.lower().startswith(prefix + "-")
    ]

    if len(candidates) == 1:
        return validate_repository_path(candidates[0])

    if len(candidates) > 1:
        names = "\n".join(f"  - {p.name}" for p in candidates)

        raise ValueError(
            f"Multiple local clones found for {repository}:\n"
            f"{names}\n"
            f"Use --repo-map {repository}=FOLDER_NAME"
        )

    raise FileNotFoundError(
        f"No local clone found for {repository}.\n"
        f"Expected a folder starting with: {prefix}"
    )


def calculate_metrics(results: list) -> dict:
    """Calculate ranking metrics for a collection of cases."""

    if not results:
        return {
            "total_cases": 0,
            "top_1_accuracy": 0.0,
            "top_5_accuracy": 0.0,
            "mean_reciprocal_rank": 0.0,
            "precision_at_5": 0.0,
        }

    total = len(results)

    return {
        "total_cases": total,
        "top_1_accuracy": round(
            sum(r["hit_at_1"] for r in results) / total, 4
        ),
        "top_5_accuracy": round(
            sum(r["hit_at_5"] for r in results) / total, 4
        ),
        "mean_reciprocal_rank": round(
            sum(r["reciprocal_rank"] for r in results) / total, 4
        ),
        "precision_at_5": round(
            sum(r["precision_at_5"] for r in results) / total, 4
        ),
    }


def evaluate_case(repo_path: Path, case: dict, top_k: int = 10) -> dict:
    """Run repository search and compare against labelled files."""

    expected_files = {
        Path(file).as_posix()
        for file in case["relevant_files"]
    }

    search_result = search_relevant_files(
        repo_path=repo_path,
        issue_text=case["issue_text"],
        top_k=top_k,
    )

    ranked_files = [
        {
            "file": item["file"],
            "score": item["score"],
            "matched_keywords": item["matched_keywords"],
            "reasons": item["reasons"],
        }
        for item in search_result["results"]
    ]

    first_relevant_rank = None

    for rank, item in enumerate(ranked_files, start=1):
        if item["file"] in expected_files:
            first_relevant_rank = rank
            break

    reciprocal_rank = (
        1 / first_relevant_rank
        if first_relevant_rank is not None
        else 0.0
    )

    top_5 = ranked_files[:5]

    relevant_in_top_5 = sum(
        1 for item in top_5
        if item["file"] in expected_files
    )

    precision_at_5 = relevant_in_top_5 / 5

    return {
        "case_id": case["id"],
        "repository": case["repository"],
        "task_type": case.get("task_type", "unspecified"),
        "difficulty": case.get("difficulty", "unspecified"),
        "task_intent": search_result.get("task_intent"),
        "keywords": search_result.get("keywords", []),
        "files_scanned": search_result.get("files_scanned", 0),
        "expected_files": sorted(expected_files),
        "first_relevant_rank": first_relevant_rank,
        "hit_at_1": first_relevant_rank == 1,
        "hit_at_5": (
            first_relevant_rank is not None
            and first_relevant_rank <= 5
        ),
        "reciprocal_rank": round(reciprocal_rank, 4),
        "precision_at_5": round(precision_at_5, 4),
        "ranked_files": ranked_files,
    }


def evaluate_dataset(repo_paths: dict, dataset: dict, top_k: int = 10) -> dict:
    """Evaluate cases across multiple repositories."""

    cases = dataset.get("cases", [])

    if not cases:
        raise ValueError("The evaluation dataset has no cases.")

    results = []

    for case in cases:
        repository = case.get("repository")

        if not repository:
            raise ValueError(
                f"Missing repository field in case: {case.get('id')}"
            )

        if repository not in repo_paths:
            raise ValueError(
                f"No resolved repository for {repository}"
            )

        print(f"Evaluating: {case['id']} ({repository})")

        result = evaluate_case(
            repo_path=repo_paths[repository],
            case=case,
            top_k=top_k,
        )

        results.append(result)

    # Aggregate metrics across all repositories.
    overall_metrics = calculate_metrics(results)

    # Calculate separate metrics for each repository.
    grouped_results = defaultdict(list)

    for result in results:
        grouped_results[result["repository"]].append(result)

    repository_metrics = {
        repository: calculate_metrics(repo_results)
        for repository, repo_results in sorted(grouped_results.items())
    }

    # Calculate metrics by task category.
    grouped_tasks = defaultdict(list)

    for result in results:
        grouped_tasks[result["task_type"]].append(result)

    task_metrics = {
        task: calculate_metrics(task_results)
        for task, task_results in sorted(grouped_tasks.items())
    }

    return {
        "benchmark_name": dataset.get("benchmark_name"),
        "benchmark_version": dataset.get("version"),
        "total_cases": len(results),
        "metrics": overall_metrics,
        "repository_metrics": repository_metrics,
        "task_metrics": task_metrics,
        "case_results": results,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate Forge repository search across repositories."
    )

    parser.add_argument(
        "--repository",
        help="Legacy mode: use one local repository folder for all cases.",
    )

    parser.add_argument(
        "--repo-map",
        action="append",
        default=[],
        metavar="OWNER/REPO=FOLDER",
        help="Map a GitHub repository to its local workspace folder. "
             "Can be supplied multiple times.",
    )

    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of search results to evaluate. Default: 10.",
    )

    args = parser.parse_args()

    if args.top_k < 5:
        raise ValueError("--top-k must be at least 5 for Precision@5.")

    with DATASET_PATH.open("r", encoding="utf-8") as file:
        dataset = json.load(file)

    repo_maps = {}

    for mapping in args.repo_map:
        if "=" not in mapping:
            raise ValueError(
                f"Invalid repository mapping: {mapping}. "
                "Expected OWNER/REPO=FOLDER."
            )

        repository, folder = mapping.split("=", 1)

        if not repository or not folder:
            raise ValueError(f"Invalid repository mapping: {mapping}")

        if "/" in folder or "\\" in folder or folder in {".", ".."}:
            raise ValueError(f"Invalid workspace folder name: {folder}")

        repo_maps[repository] = folder

    repositories = {
        case["repository"]
        for case in dataset.get("cases", [])
    }

    if args.repository:
        if len(repositories) != 1:
            raise ValueError(
                "--repository can only be used when all benchmark "
                "cases belong to one GitHub repository."
            )

        repository = next(iter(repositories))
        repo_maps[repository] = args.repository

    repo_paths = {}

    for repository in sorted(repositories):
        repo_paths[repository] = resolve_repository(
            repository,
            repo_maps,
        )

        print(
            f"Repository ready: {repository} "
            f"-> {repo_paths[repository].name}"
        )

    report = evaluate_dataset(
        repo_paths=repo_paths,
        dataset=dataset,
        top_k=args.top_k,
    )

    print("\n========== FORGE EVALUATION ==========")
    print(json.dumps(report, indent=2))
    print("======================================")

    report_path = BASE_DIR / "evaluation_report.json"

    with report_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)

    print(f"\nReport saved to: {report_path}")


if __name__ == "__main__":
    main()
