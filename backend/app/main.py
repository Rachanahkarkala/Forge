from fastapi import FastAPI
from app.github.client import get_github_issue
from pydantic import BaseModel, HttpUrl
from fastapi import HTTPException
from app.repositories.inspector import analyze_repository
from pathlib import Path
from pydantic import Field
from app.repositories.inspector import (
    analyze_repository,
    parse_github_url,
)
from app.repositories.search import search_relevant_files
from app.repositories.inspector import WORKSPACE_DIR

class RepositoryRequest(BaseModel):
    repository_url: HttpUrl

class InvestigationRequest(BaseModel):
    repository_url: HttpUrl
    issue_number: int = Field(gt=0)

class CodeSearchRequest(BaseModel):
    repository_name: str
    issue_text: str = Field(min_length=3)
    top_k: int = Field(default=10, ge=1, le=20)

app = FastAPI(
    title="Forge API",
    description="AI-powered software engineering agent",
    version="0.2.0"
)


@app.get("/")
async def root():
    return {
        "message": "Welcome to Forge!",
        "status": "operational"
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "forge-backend"
    }


@app.get("/github/issues/{owner}/{repo}/{issue_number}")
async def fetch_issue(
    owner: str,
    repo: str,
    issue_number: int
):
    issue = await get_github_issue(
        owner,
        repo,
        issue_number
    )

    return {
        "success": True,
        "data": issue
    }

@app.post("/repositories/inspect")
async def inspect_repo(request: RepositoryRequest):

    url = str(request.repository_url)

    try:
        result = analyze_repository(url)
        return result

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    except RuntimeError as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Repository inspection failed."
        )
@app.post("/repositories/search")
async def search_repository(request: CodeSearchRequest):

    workspace_root = WORKSPACE_DIR.resolve()

    # Repository names must be simple directory names.
    name = request.repository_name

    if (
        name in {".", ".."}
        or "/" in name
        or "\\" in name
        or not name
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid repository name."
        )

    repo_path = (workspace_root / name).resolve()

    # Prevent selecting paths outside our workspace.
    if repo_path.parent != workspace_root:
        raise HTTPException(
            status_code=400,
            detail="Invalid repository path."
        )

    if not repo_path.is_dir():
        raise HTTPException(
            status_code=404,
            detail="Repository not found. Inspect it first."
        )

    result = search_relevant_files(
        repo_path=repo_path,
        issue_text=request.issue_text,
        top_k=request.top_k,
    )

    return {
        "success": True,
        "repository_name": name,
        "analysis": result,
    }
@app.post("/investigate")
async def investigate(request: InvestigationRequest):

    url = str(request.repository_url)

    try:
        # 1. Identify repository owner and name
        owner, repo = parse_github_url(url)

        # 2. Fetch the GitHub issue
        issue = await get_github_issue(
            owner,
            repo,
            request.issue_number
        )

        # 3. Clone and inspect the repository
        repository = analyze_repository(url)

        repo_path = Path(
            repository["analysis"]["repository_path"]
        )

        # 4. Prepare issue text
        issue_text = " ".join([
            issue.get("title") or "",
            issue.get("description") or "",
        ])

        # 5. Search relevant source files
        code_analysis = search_relevant_files(
            repo_path=repo_path,
            issue_text=issue_text,
            top_k=10,
        )

        # 6. Return consolidated report
        return {
            "success": True,
            "issue": issue,
            "repository": repository,
            "code_analysis": code_analysis,
        }

    except HTTPException:
        raise

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    except RuntimeError as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Investigation failed."
        )