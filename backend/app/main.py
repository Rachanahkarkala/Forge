
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, HttpUrl

from app.github.client import get_github_issue
from app.repositories.inspector import (
    analyze_repository,
    parse_github_url,
    WORKSPACE_DIR,
)
from app.repositories.search import search_relevant_files
from app.agents.planner import build_investigation_plan
from app.agents.impact_analyzer import analyze_impact
from app.agents.investigator import investigate_issue
from app.sandbox.environment_runner import prepare_and_run_python_tests


# --------------------------------------------------
# REQUEST MODELS
# --------------------------------------------------

class RepositoryRequest(BaseModel):
    repository_url: HttpUrl


class InvestigationRequest(BaseModel):
    repository_url: HttpUrl
    issue_number: int = Field(gt=0)
    execute_tests: bool = False


class CodeSearchRequest(BaseModel):
    repository_name: str
    issue_text: str = Field(min_length=3)
    top_k: int = Field(default=10, ge=1, le=20)


class TestExecutionRequest(BaseModel):
    repository_name: str
    test_paths: list[str] | None = None
    timeout_seconds: int = Field(default=180, ge=1, le=600)


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def resolve_workspace_repository(repository_name: str) -> Path:
    """Resolve a repository name safely inside Forge's workspace."""

    workspace_root = WORKSPACE_DIR.resolve()

    if (
        not repository_name
        or repository_name in {".", ".."}
        or "/" in repository_name
        or "\\" in repository_name
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid repository name.",
        )

    repo_path = (workspace_root / repository_name).resolve()

    if repo_path.parent != workspace_root:
        raise HTTPException(
            status_code=400,
            detail="Invalid repository path.",
        )

    if not repo_path.is_dir():
        raise HTTPException(
            status_code=404,
            detail="Repository not found. Inspect it first.",
        )

    return repo_path


def run_tests_safely(
    repo_path: Path,
    test_paths: list[str] | None = None,
    timeout_seconds: int = 180,
) -> dict:
    """Prepare the project's environment and execute tests in Docker."""

    try:
        return prepare_and_run_python_tests(
            repo_path=repo_path,
            test_paths=test_paths,
            install_timeout=600,
            test_timeout=timeout_seconds,
        )

    except Exception as exc:
        return {
            "status": "execution_error",
            "execution_performed": False,
            "test_execution_performed": False,
            "error": str(exc),
            "selected_test_files": test_paths or [],
            "original_repository_modified": False,
        }


# --------------------------------------------------
# FASTAPI APPLICATION
# --------------------------------------------------

app = FastAPI(
    title="Forge API",
    description="Evidence-driven AI software engineering agent",
    version="0.6.0",
)


@app.get("/")
async def root():
    return {
        "message": "Welcome to Forge!",
        "status": "operational",
        "version": "0.6.0",
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "forge-backend",
    }


# --------------------------------------------------
# GITHUB ISSUE RETRIEVAL
# --------------------------------------------------

@app.get("/github/issues/{owner}/{repo}/{issue_number}")
async def fetch_issue(owner: str, repo: str, issue_number: int):

    issue = await get_github_issue(
        owner,
        repo,
        issue_number,
    )

    return {
        "success": True,
        "data": issue,
    }


# --------------------------------------------------
# REPOSITORY INSPECTION
# --------------------------------------------------

@app.post("/repositories/inspect")
async def inspect_repo(request: RepositoryRequest):

    try:
        return analyze_repository(str(request.repository_url))

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Repository inspection failed.",
        )


# --------------------------------------------------
# REPOSITORY CODE SEARCH
# --------------------------------------------------

@app.post("/repositories/search")
async def search_repository(request: CodeSearchRequest):

    repo_path = resolve_workspace_repository(
        request.repository_name
    )

    result = search_relevant_files(
        repo_path=repo_path,
        issue_text=request.issue_text,
        top_k=request.top_k,
    )

    return {
        "success": True,
        "repository_name": request.repository_name,
        "analysis": result,
    }


# --------------------------------------------------
# DOCKER TEST EXECUTION
# --------------------------------------------------

@app.post("/repositories/test")
async def test_repository(request: TestExecutionRequest):

    repo_path = resolve_workspace_repository(
        request.repository_name
    )

    result = run_tests_safely(
        repo_path=repo_path,
        test_paths=request.test_paths,
        timeout_seconds=request.timeout_seconds,
    )

    failed_statuses = {
        "execution_error",
        "timeout",
        "environment_setup_failed",
        "failed",
    }

    return {
        "success": result.get("status") not in failed_statuses,
        "repository_name": request.repository_name,
        "test_execution": result,
    }


# --------------------------------------------------
# COMPLETE AI ISSUE INVESTIGATION
# --------------------------------------------------


@app.post("/investigate")
async def investigate(request: InvestigationRequest):

    url = str(request.repository_url)

    try:
        # 1. Identify repository owner and name.
        owner, repo = parse_github_url(url)

        # 2. Fetch GitHub issue or PR metadata.
        issue = await get_github_issue(
            owner,
            repo,
            request.issue_number,
        )

        # 3. Clone and inspect repository.
        repository = analyze_repository(url)

        repo_path = Path(
            repository["analysis"]["repository_path"]
        )

        # 4. Prepare issue text.
        issue_text = " ".join([
            issue.get("title") or "",
            issue.get("description") or "",
        ]).strip()

        if len(issue_text) < 3:
            raise HTTPException(
                status_code=400,
                detail="GitHub issue does not contain enough text.",
            )

        # 5. Search relevant source files.
        code_analysis = search_relevant_files(
            repo_path=repo_path,
            issue_text=issue_text,
            top_k=10,
        )

        # 6. Generate investigation plan.
        investigation_plan = build_investigation_plan(
            issue=issue,
            search_result=code_analysis,
        )

        # 7. Analyse potential code impact.
        impact_analysis = analyze_impact(
            repo_path=repo_path,
            issue_text=issue_text,
            top_k=10,
        )

        # 8. Execute relevant tests only when requested.
        test_execution = {
            "status": "not_requested",
            "execution_performed": False,
            "test_execution_performed": False,
            "execution_attempted": False,
            "message": "Set execute_tests=true to run relevant tests.",
        }

        if request.execute_tests:

            selected_tests = impact_analysis.get(
                "potentially_affected_tests",
                [],
            )

            if not selected_tests:

                test_execution = {
                    "status": "no_relevant_tests_selected",
                    "execution_performed": False,
                    "test_execution_performed": False,
                    "execution_attempted": False,
                    "message": (
                        "Forge could not identify relevant test files. "
                        "No tests were executed."
                    ),
                }

            else:

                test_execution = run_tests_safely(
                    repo_path=repo_path,
                    test_paths=selected_tests,
                )

                # The runner may be invoked even if environment setup
                # fails. Distinguish that from tests actually running.
                test_execution["execution_attempted"] = True

                tests_actually_ran = bool(
                    test_execution.get(
                        "test_execution_performed",
                        test_execution.get(
                            "execution_performed",
                            False,
                        ),
                    )
                )

                test_execution["execution_performed"] = (
                    tests_actually_ran
                )
                test_execution["test_execution_performed"] = (
                    tests_actually_ran
                )

        # 9. Send repository, GitHub and test evidence to the LLM.
        ai_investigation = await investigate_issue(
            impact_report=impact_analysis,
            issue_metadata=issue,
            test_execution=test_execution,
        )

        # 10. Return consolidated report.
        return {
            "success": True,

            "issue": issue,
            "repository": repository,
            "code_analysis": code_analysis,
            "investigation_plan": investigation_plan,

            "impact_analysis": {
                "summary": impact_analysis["summary"],
                "impacted_files": impact_analysis["impacted_files"],
                "potentially_affected_tests": (
                    impact_analysis["potentially_affected_tests"]
                ),
                "dependency_graph_summary": (
                    impact_analysis["dependency_graph_summary"]
                ),
            },

            "test_execution": test_execution,
            "ai_investigation": ai_investigation,

            "execution_status": {
                "repository_cloned": True,
                "code_analysis_completed": True,
                "impact_analysis_completed": True,
                "ai_investigation_completed": True,
                "tests_requested": request.execute_tests,
                "tests_attempted": test_execution.get(
                    "execution_attempted",
                    False,
                ),
                "tests_executed": test_execution.get(
                    "test_execution_performed",
                    False,
                ),
                "fix_generated": False,
                "pull_request_created": False,
            },
        }

    except HTTPException:
        raise

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        )

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Issue investigation failed. Check backend logs.",
        )
