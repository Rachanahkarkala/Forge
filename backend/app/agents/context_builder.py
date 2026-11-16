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

    Rejects paths outside the repository and symlinks,
    including symlinked parent directories.
    """

    try:
        repo_path = Path(repo_path).resolve()

        relative = Path(relative_path)

        if relative.is_absolute() or ".." in relative.parts:
            return None, "Invalid repository-relative path."

        candidate = repo_path / relative

        # Check every component before resolving the path.
        current = repo_path

        for part in relative.parts:
            current = current / part

            if current.is_symlink():
                return None, "Symlinks are not allowed."

        target = candidate.resolve()

        try:
            target.relative_to(repo_path)
        except ValueError:
            return None, "Path is outside the repository."

        if not target.is_file():
            return None, "File does not exist."

        if target.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return None, "Unsupported file type."

        if target.stat().st_size > MAX_FILE_SIZE:
            return None, "File exceeds the size limit."

        content = target.read_text(
            encoding="utf-8",
            errors="replace",
        )

        return content, None

    except (OSError, ValueError):
        return None, "Unable to safely read file."


# --------------------------------------------------
# PYTHON STRUCTURAL ANALYSIS
# --------------------------------------------------

class PythonStructureVisitor(ast.NodeVisitor):
    """
    Extract functions, async functions, methods,
    classes and imports without executing source code.

    Maintains class and function context so nested
    definitions can be identified.
    """

    def __init__(self):
        self.imports = []
        self.symbols = []
        self.functions = []

        self.class_stack = []
        self.function_stack = []

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append(alias.name)

    def visit_ImportFrom(self, node):
        module = node.module or ""

        self.imports.append(module)

        # Preserve imported names as additional information.
        for alias in node.names:
            self.imports.append(
                f"{module}.{alias.name}".strip(".")
            )

    def visit_ClassDef(self, node):
        class_name = ".".join(
            self.class_stack + [node.name]
        )

        self.symbols.append({
            "name": node.name,
            "qualified_name": class_name,
            "type": "class",
            "line_start": node.lineno,
            "line_end": getattr(
                node, "end_lineno", node.lineno
            ),
            "parameters": [],
            "parent_class": (
                self.class_stack[-1]
                if self.class_stack
                else None
            ),
        })

        self.class_stack.append(node.name)

        for child in node.body:
            self.visit(child)

        self.class_stack.pop()

    def visit_FunctionDef(self, node):
        self._visit_function(node, "function")

    def visit_AsyncFunctionDef(self, node):
        self._visit_function(node, "async_function")

    def _visit_function(self, node, function_type):
        parameters = [
            arg.arg
            for arg in (
                list(node.args.posonlyargs)
                + list(node.args.args)
                + list(node.args.kwonlyargs)
            )
        ]

        if node.args.vararg:
            parameters.append(
                "*" + node.args.vararg.arg
            )

        if node.args.kwarg:
            parameters.append(
                "**" + node.args.kwarg.arg
            )

        parent_class = (
            self.class_stack[-1]
            if self.class_stack
            else None
        )

        qualified_parts = (
            self.class_stack
            + self.function_stack
            + [node.name]
        )

        qualified_name = ".".join(qualified_parts)

        function_info = {
            "name": node.name,
            "qualified_name": qualified_name,
            "type": function_type,
            "line_start": node.lineno,
            "line_end": getattr(
                node, "end_lineno", node.lineno
            ),
            "parameters": parameters,
            "parent_class": parent_class,
            "is_method": parent_class is not None,
            "is_async": isinstance(
                node, ast.AsyncFunctionDef
            ),
        }

        self.functions.append(function_info)

        self.symbols.append(function_info.copy())

        # Visit nested functions without losing their context.
        self.function_stack.append(node.name)

        for child in node.body:
            self.visit(child)

        self.function_stack.pop()


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
            "functions": [],
        }

    visitor = PythonStructureVisitor()
    visitor.visit(tree)

    return {
        "parse_success": True,
        "parse_error": None,
        "imports": sorted(set(visitor.imports)),
        "symbols": sorted(
            visitor.symbols,
            key=lambda item: item["line_start"],
        ),
        "functions": sorted(
            visitor.functions,
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

    valid_keywords = [
        str(keyword)
        for keyword in keywords
        if keyword
    ]

    patterns = [
        (
            keyword,
            re.compile(
                rf"(?<![a-zA-Z0-9_])"
                rf"{re.escape(keyword)}"
                rf"(?![a-zA-Z0-9_])",
                re.IGNORECASE,
            ),
        )
        for keyword in valid_keywords
    ]

    for index, line in enumerate(lines):
        if any(
            pattern.search(line)
            for _, pattern in patterns
        ):
            matched_lines.add(index)

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

        snippet = "\n".join(lines[start:end])

        if len(snippet) > MAX_SNIPPET_LENGTH:
            snippet = snippet[:MAX_SNIPPET_LENGTH]

        matched_keywords = [
            keyword
            for keyword, pattern in patterns
            if pattern.search(lines[index])
        ]

        snippets.append({
            "line_start": start + 1,
            "line_end": end,
            "matched_line": index + 1,
            "matched_keywords": matched_keywords,
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

    Extracts:
    - Imports
    - Classes
    - Functions
    - Async functions
    - Methods
    - Evidence snippets

    This function only reads source files.
    It does not execute repository code.
    """

    repo_path = Path(repo_path).resolve()

    if not repo_path.is_dir():
        raise ValueError(
            "Repository directory does not exist."
        )

    if not isinstance(search_result, dict):
        raise ValueError(
            "Search result must be a dictionary."
        )

    keywords = keywords or search_result.get(
        "keywords", []
    )

    ranked_files = search_result.get("results", [])

    if not isinstance(ranked_files, list):
        ranked_files = []

    max_files = max(0, int(max_files))

    selected_files = ranked_files[:max_files]

    file_contexts = []
    errors = []
    total_chars = 0

    for item in selected_files:

        if not isinstance(item, dict):
            continue

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
            "functions": structure["functions"],
            "evidence_snippets": snippets,
        }

        # Keep context bounded without discarding an entire file.
        remaining_budget = MAX_TOTAL_CONTEXT_CHARS - total_chars

        if remaining_budget <= 0:
            break

        # Reduce oversized structures progressively.
        for limit in (30, 20, 12, 8, 5, 2, 0):
            file_context["symbols"] = structure["symbols"][:limit]
            file_context["functions"] = structure["functions"][:limit]
            estimated_size = len(str(file_context))

            if estimated_size <= remaining_budget:
                break

        # If even the reduced context is too large, preserve
        # a minimal evidence record rather than dropping the file.
        if estimated_size > remaining_budget:
            file_context["symbols"] = []
            file_context["functions"] = []
            file_context["imports"] = file_context["imports"][:10]
            file_context["evidence_snippets"] = (
                file_context["evidence_snippets"][:3]
            )
            estimated_size = len(str(file_context))

        if estimated_size > remaining_budget:
            continue

        file_contexts.append(file_context)
        total_chars += estimated_size

    total_symbols = sum(
        len(item["symbols"])
        for item in file_contexts
    )

    total_functions = sum(
        len(item["functions"])
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
        "total_functions_extracted": total_functions,
        "total_evidence_snippets": total_snippets,
        "context_characters": total_chars,
        "files": file_contexts,
        "errors": errors,
        "execution_performed": False,
    }