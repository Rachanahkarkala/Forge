import httpx
from fastapi import HTTPException


async def get_github_issue(owner: str, repo: str, issue_number: int):
    url = (
        f"https://api.github.com/repos/"
        f"{owner}/{repo}/issues/{issue_number}"
    )

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Forge-AI-Agent"
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, headers=headers)

    if response.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail="GitHub repository or issue not found."
        )

    if response.status_code == 403:
        raise HTTPException(
            status_code=403,
            detail="GitHub API rate limit or access restriction."
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail="Unable to fetch issue from GitHub."
        )

    data = response.json()

    return {
        "title": data["title"],
        "description": data["body"],
        "state": data["state"],
        "author": data["user"]["login"],
        "item_type": (
            "pull_request" if "pull_request" in data else "issue"
        ),
        "is_pull_request": "pull_request" in data,
        "issue_number": data["number"],
        "issue_url": data["html_url"],
        "labels": [label["name"] for label in data["labels"]],
        "created_at": data["created_at"],
        "updated_at": data["updated_at"]
    }