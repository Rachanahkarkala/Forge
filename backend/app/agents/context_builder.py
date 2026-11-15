
import ast
import re
from pathlib import Path


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

MAX_FILES = 5
MAX_FILE_SIZE = 250_000
MAX_SNIPPETS_PER_FILE = 8
MAX_SNIPPET_LENGTH = 1200
MAX_TOTAL_CONTEXT_CHARS = 20_000

SUPPORTED_EXTENSIONS = {".py"}


# --------------------------------------------------
# SAFE FILE ACCESS
# --------------------------------------------------

def read_repository_file(
    repo_path: Path,
    relative_path: str,
) -> tuple[str | None, str | None]:
    """
    Read a repository file without executing it.

    Rejects paths outside the repository and symlinks.
    """

    repo_path = Path(repo_path).resolve()
    target = (repo_path / relative_path).resolve()

    try:
        target.relative_to(repo_path)
    except ValueError:
        return None, "Path is outside the repository."

    if target.is_symlink() or not target.is_file():
        return None, "File is missing or is a symlink."

    if target.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return None, "Unsupported file type."

    try:
        if target.stat().st_size > MAX_FILE_SIZE:
            return None, "File exceeds the size limit."

        content = target.read_text(
            encoding="utf-8",
            errors="replace",
        )

        return content, None

    except OSError:
        return None, "Unable to read file."


# --------------------------------------------------
# PYTHON STRUCTURAL ANALYSIS
# --------------------------------------------------

def extract_python_structure(content: str) -> dict:
    """
    Extract imports, functions, classes and methods
    using Python AST without executing source code.
    """

    try:
        tree = ast.parse(content)

    except SyntaxError as error:
        return {
            "parse_success": False,
            "parse_error": str(error),
            "imports": [],
            "symbols": [],
        }

    imports = []
    symbols = []

    for node in ast.walk(tree):

        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""

            imports.append(module)

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append({
                "name": node.name,
                "type": "function",
                "line_start": node.lineno,
                "line_end": node.end_lineno,
                "parameters": [
                    arg.arg for arg in node.args.args
                ],
            })

        elif isinstance(node, ast.ClassDef):
            symbols.append({
                "name": node.name,
                "type": "class",
                "line_start": node.lineno,
                "line_end": node.end_lineno,
                "parameters": [],
            })

    return {
        "parse_success": True,
        "parse_error": None,
        "imports": sorted(set(imports)),
        "symbols": sorted(
            symbols,
            key=lambda item: item["line_start"],
        ),
    }


# --------------------------------------------------
# RELEVANT CODE EXTRACTION
# --------------------------------------------------

def extract_evidence_snippets(
    content: str,
    keywords: list[str],
) -> list[dict]:
    """
    Find source lines containing issue-related keywords.

    Includes surrounding context to make snippets useful.
    """

    lines = content.splitlines()
    matched_lines = set()

    for index, line in enumerate(lines):

        for keyword in keywords:

            pattern = (
                rf"(?<![a-zA-Z0-9_])"
                rf"{re.escape(keyword)}"
                rf"(?![a-zA-Z0-9_])"
            )

            if re.search(pattern, line, re.IGNORECASE):
                matched_lines.add(index)
                break

    snippets = []
    included_lines = set()

    for index in sorted(matched_lines):

        if len(snippets) >= MAX_SNIPPETS_PER_FILE:
            break

        start = max(0, index - 2)
        end = min(len(lines), index + 3)

        snippet_indexes = set(range(start, end))

        if snippet_indexes.issubset(included_lines):
            continue

        snippet = "\n".join(
            lines[start:end]
        )

        if len(snippet) > MAX_SNIPPET_LENGTH:
            snippet = snippet[:MAX_SNIPPET_LENGTH]

        snippets.append({
            "line_start": start + 1,
            "line_end": end,
            "matched_line": index + 1,
            "matched_keywords": [
                keyword
                for keyword in keywords
                if re.search(
                    rf"(?<![a-zA-Z0-9_])"
                    rf"{re.escape(keyword)}"
                    rf"(?![a-zA-Z0-9_])",
                    lines[index],
                    re.IGNORECASE,
                )
            ],
            "content": snippet,
        })

        included_lines.update(snippet_indexes)

    return snippets


# --------------------------------------------------
# MAIN CONTEXT BUILDER
# --------------------------------------------------

def build_code_context(
    repo_path: Path,
    search_result: dict,
    keywords: list[str] | None = None,
    max_files: int = MAX_FILES,
) -> dict:
    """
    Build structured evidence from the highest-ranked
    repository search results.

    This function only reads source files.
    It does not execute repository code.
    """

    repo_path = Path(repo_path).resolve()

    if not repo_path.is_dir():
        raise ValueError("Repository directory does not exist.")

    keywords = keywords or search_result.get("keywords", [])

    ranked_files = search_result.get("results", [])

    selected_files = ranked_files[:max_files]

    file_contexts = []
    errors = []
    total_chars = 0

    for item in selected_files:

        relative_path = item.get("file")

        if not relative_path:
            continue

        content, error = read_repository_file(
            repo_path,
            relative_path,
        )

        if error:
            errors.append({
                "file": relative_path,
                "error": error,
            })
            continue

        structure = extract_python_structure(content)

        snippets = extract_evidence_snippets(
            content,
            keywords,
        )

        file_context = {
            "file": relative_path,
            "search_score": item.get("score", 0),
            "matched_keywords": item.get(
                "matched_keywords", []
            ),
            "parse_success": structure["parse_success"],
            "parse_error": structure["parse_error"],
            "imports": structure["imports"],
            "symbols": structure["symbols"],
            "evidence_snippets": snippets,
        }

        estimated_size = len(str(file_context))

        if total_chars + estimated_size > MAX_TOTAL_CONTEXT_CHARS:
            break

        file_contexts.append(file_context)
        total_chars += estimated_size

    total_symbols = sum(
        len(item["symbols"])
        for item in file_contexts
    )

    total_snippets = sum(
        len(item["evidence_snippets"])
        for item in file_contexts
    )

    return {
        "repository_path": str(repo_path),
        "files_considered": len(selected_files),
        "files_analyzed": len(file_contexts),
        "total_symbols_extracted": total_symbols,
        "total_evidence_snippets": total_snippets,
        "context_characters": total_chars,
        "files": file_contexts,
        "errors": errors,
        "execution_performed": False,
    }
