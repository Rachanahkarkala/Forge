
import os
import re
import uuid
import subprocess

from pathlib import Path
from collections import Counter


# Store cloned repositories inside the backend workspace
BASE_DIR = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = BASE_DIR.parent / "workspaces"

# Directories that should not be inspected
IGNORED_DIRS = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    ".next",
    "dist",
    "build",
    ".idea",
    ".vscode",
}

# File extensions used for language detection
LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript React",
    ".ts": "TypeScript",
    ".tsx": "TypeScript React",
    ".java": "Java",
    ".cpp": "C++",
    ".c": "C",
    ".go": "Go",
    ".rs": "Rust",
    ".php": "PHP",
    ".rb": "Ruby",
    ".html": "HTML",
    ".css": "CSS",
    ".sql": "SQL",
    ".sh": "Shell",
}


def parse_github_url(url: str):
    """
    Accept only public GitHub repository URLs.
    """

    pattern = (
        r"^https://github\.com/"
        r"([A-Za-z0-9_.-]+)/"
        r"([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
    )

    match = re.fullmatch(pattern, url.strip())

    if not match:
        raise ValueError(
            "Invalid GitHub repository URL. "
            "Use https://github.com/owner/repository"
        )

    owner, repo = match.groups()

    if owner in {".", ".."} or repo in {".", ".."}:
        raise ValueError("Invalid repository path.")

    return owner, repo


def clone_repository(url: str):
    """
    Clone a public GitHub repository into a unique workspace.

    Repository code is cloned but never executed here.
    """

    owner, repo = parse_github_url(url)

    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    destination = WORKSPACE_DIR / (
        f"{owner}-{repo}-{uuid.uuid4().hex[:8]}"
    )

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    command = [
        "git",
        "-c", "protocol.ext.allow=never",
        "clone",
        "--depth", "1",
        "--no-recurse-submodules",
        url,
        str(destination),
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            shell=False,
        )

        if result.returncode != 0:
            raise RuntimeError(
                result.stderr[-2000:] or "Git clone failed."
            )

        return destination

    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "Repository cloning timed out after 120 seconds."
        )

    except Exception:
        if destination.exists():
            import shutil
            shutil.rmtree(destination, ignore_errors=True)
        raise


def inspect_repository(repo_path: Path):
    """
    Inspect repository structure and identify technologies.
    Does not execute or import repository code.
    """

    file_paths = []
    extensions = Counter()
    manifests = []
    root_files = []

    max_files = 10000

    important_files = {
        "requirements.txt",
        "pyproject.toml",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "readme.md",
    }

    for root, dirs, files in os.walk(repo_path):

        # Skip generated and hidden directories
        dirs[:] = [
            d for d in dirs
            if d not in IGNORED_DIRS and not d.startswith(".")
        ]

        for filename in files:

            path = Path(root) / filename
            relative_path = path.relative_to(repo_path)

            if path.is_symlink():
                continue

            file_paths.append(str(relative_path))
            extensions[path.suffix.lower()] += 1

            if str(relative_path).count(os.sep) == 0:
                root_files.append(filename)

            if filename.lower() in important_files:
                manifests.append(str(relative_path))

            if len(file_paths) >= max_files:
                break

        if len(file_paths) >= max_files:
            break

    languages = Counter()

    for extension, count in extensions.items():
        language = LANGUAGES.get(extension)

        if language:
            languages[language] += count

    frameworks = []

    manifest_names = {Path(m).name.lower() for m in manifests}

    if "requirements.txt" in manifest_names:
        frameworks.append("Python dependencies")

    if "pyproject.toml" in manifest_names:
        frameworks.append("Python project configuration")

    if "package.json" in manifest_names:
        frameworks.append("Node.js project")

    if "cargo.toml" in manifest_names:
        frameworks.append("Rust project")

    if "go.mod" in manifest_names:
        frameworks.append("Go project")

    if "dockerfile" in manifest_names:
        frameworks.append("Docker")

    return {
        "repository_path": str(repo_path),
        "total_files_inspected": len(file_paths),
        "file_limit_reached": len(file_paths) >= max_files,
        "languages": dict(languages.most_common()),
        "framework_indicators": frameworks,
        "root_files": root_files[:100],
        "dependency_files": manifests,
        "file_tree": file_paths[:300],
    }


def analyze_repository(url: str):
    """
    Complete repository cloning and inspection workflow.
    """

    repo_path = clone_repository(url)

    report = inspect_repository(repo_path)

    return {
        "success": True,
        "repository_url": url,
        "repository_name": repo_path.name,
        "analysis": report,
    }
