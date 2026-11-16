
import ast
from collections import defaultdict, deque
from pathlib import Path


IGNORED_DIRS = {
    ".git",
    "venv",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
}

MAX_FILE_SIZE = 250_000

# Resource limits for local development.
DEFAULT_MAX_FILES = 120
DEFAULT_MAX_IMPORT_DEPTH = 1


def get_python_files(repo_path: Path) -> list[Path]:
    """Collect Python files without entering ignored directories."""

    python_files = []

    for path in repo_path.rglob("*.py"):
        try:
            relative = path.relative_to(repo_path)

            if any(part in IGNORED_DIRS for part in relative.parts):
                continue

            if path.is_symlink() or not path.is_file():
                continue

            if path.stat().st_size > MAX_FILE_SIZE:
                continue

            python_files.append(path)

        except (OSError, ValueError):
            continue

    return sorted(python_files)


def get_module_name(repo_path: Path, file_path: Path) -> str:
    """Convert a Python file path into an approximate module name."""

    relative = file_path.relative_to(repo_path)
    parts = list(relative.with_suffix("").parts)

    if parts and parts[-1] == "__init__":
        parts.pop()

    return ".".join(parts)


def extract_imports(tree: ast.AST) -> list[dict]:
    """Extract import statements and imported names."""

    imports = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({
                    "module": alias.name,
                    "imported_names": [],
                    "level": 0,
                    "line": node.lineno,
                })

        elif isinstance(node, ast.ImportFrom):
            imports.append({
                "module": node.module or "",
                "imported_names": [
                    alias.name
                    for alias in node.names
                    if alias.name != "*"
                ],
                "level": node.level,
                "line": node.lineno,
            })

    return imports


class DependencyVisitor(ast.NodeVisitor):
    """Collect function definitions and syntactic function calls."""

    def __init__(self):
        self.functions = []
        self.calls = []
        self.scope = []

    def visit_ClassDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node):
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node):
        self._visit_function(node)

    def _visit_function(self, node):
        qualified_name = ".".join(self.scope + [node.name])

        self.functions.append({
            "name": node.name,
            "qualified_name": qualified_name,
            "line": node.lineno,
            "end_line": node.end_lineno,
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "arguments": [
                arg.arg for arg in node.args.args
            ],
        })

        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Call(self, node):
        call_name = self.get_call_name(node.func)

        if call_name:
            self.calls.append({
                "name": call_name,
                "line": node.lineno,
                "scope": ".".join(self.scope) or "<module>",
            })

        self.generic_visit(node)

    @staticmethod
    def get_call_name(node):
        if isinstance(node, ast.Name):
            return node.id

        if isinstance(node, ast.Attribute):
            parts = []
            current = node

            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value

            if isinstance(current, ast.Name):
                parts.append(current.id)

            return ".".join(reversed(parts))

        return None


def analyze_python_file(
    repo_path: Path,
    file_path: Path,
) -> dict:
    """Extract static dependency information from one Python file."""

    relative_path = file_path.relative_to(repo_path).as_posix()
    module_name = get_module_name(repo_path, file_path)

    try:
        source = file_path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        tree = ast.parse(source, filename=str(file_path))

        visitor = DependencyVisitor()
        visitor.visit(tree)

        return {
            "file": relative_path,
            "module": module_name,
            "imports": extract_imports(tree),
            "functions": visitor.functions,
            "calls": visitor.calls,
            "parse_success": True,
            "error": None,
        }

    except (SyntaxError, OSError, ValueError) as exc:
        return {
            "file": relative_path,
            "module": module_name,
            "imports": [],
            "functions": [],
            "calls": [],
            "parse_success": False,
            "error": str(exc),
        }


def _module_index(
    repo_path: Path,
    python_files: list[Path],
) -> dict[str, Path]:
    """Create a module-to-file index without parsing file contents."""

    index = {}

    for path in python_files:
        module = get_module_name(repo_path, path)

        if module:
            index[module] = path

        # Also map package __init__.py files to their package.
        if path.name == "__init__.py":
            package = ".".join(path.relative_to(repo_path).parts[:-1])
            if package:
                index[package] = path

    return index


def _resolve_import_modules(
    current_file: Path,
    repo_path: Path,
    imported: dict,
) -> list[str]:
    """Resolve absolute and relative imports to possible module names."""

    module = imported.get("module", "")
    level = imported.get("level", 0)

    if level == 0:
        base = module
    else:
        current_module = get_module_name(repo_path, current_file)

        if current_file.name == "__init__.py":
            package_parts = current_module.split(".") if current_module else []
        else:
            package_parts = current_module.split(".")[:-1]

        # level=1 means current package; level=2 means parent package.
        remove_count = max(level - 1, 0)

        if remove_count:
            package_parts = package_parts[:-remove_count]

        if module:
            package_parts.extend(module.split("."))

        base = ".".join(package_parts)

    candidates = []

    if base:
        candidates.append(base)

    for name in imported.get("imported_names", []):
        if base:
            candidates.append(f"{base}.{name}")

    return candidates


def _select_analysis_files(
    repo_path: Path,
    all_python_files: list[Path],
    target_files: list[str] | None,
    max_files: int,
    max_import_depth: int,
) -> tuple[list[Path], int]:
    """
    Select relevant files and their directly imported internal dependencies.

    Only selected files are parsed. The repository is indexed by path so
    imports can be resolved without parsing every file.
    """

    if max_files < 1:
        raise ValueError("max_files must be at least 1.")

    if max_import_depth < 0:
        raise ValueError("max_import_depth cannot be negative.")

    module_index = _module_index(repo_path, all_python_files)
    path_index = {
        path.relative_to(repo_path).as_posix(): path
        for path in all_python_files
    }

    selected = []
    selected_set = set()
    queue = deque()

    def add_file(path: Path, depth: int):
        relative = path.relative_to(repo_path).as_posix()

        if relative in selected_set or len(selected) >= max_files:
            return

        selected_set.add(relative)
        selected.append(path)
        queue.append((path, depth))

    # Explicit targets are always considered first.
    if target_files:
        for target in target_files:
            if not isinstance(target, str):
                continue

            normalized = target.replace("\\", "/").lstrip("./")
            path = path_index.get(normalized)

            if path is not None:
                add_file(path, 0)

    # No targets supplied: use a bounded fallback rather than parsing
    # the entire repository.
    if not selected:
        fallback = sorted(
            all_python_files,
            key=lambda path: (
                0 if "test" in path.parts or path.name.startswith("test_") else 1,
                len(path.parts),
                path.as_posix(),
            ),
        )

        for path in fallback[:max_files]:
            add_file(path, 0)

    # Follow imports only up to the configured depth.
    while queue and len(selected) < max_files:
        current_file, depth = queue.popleft()

        if depth >= max_import_depth:
            continue

        result = analyze_python_file(repo_path, current_file)

        if not result["parse_success"]:
            continue

        for imported in result["imports"]:
            for candidate in _resolve_import_modules(
                current_file,
                repo_path,
                imported,
            ):
                target = module_index.get(candidate)

                if target is not None:
                    add_file(target, depth + 1)

                if len(selected) >= max_files:
                    break

            if len(selected) >= max_files:
                break

    return selected, len(all_python_files)


def resolve_internal_imports(file_results: list[dict]) -> list[dict]:
    """Connect imports to modules found in the analyzed file set."""

    module_to_file = {}

    for result in file_results:
        if result["parse_success"]:
            module_to_file[result["module"]] = result["file"]

    edges = []

    for result in file_results:
        if not result["parse_success"]:
            continue

        for imported in result["imports"]:
            # Relative import resolution is handled during file selection.
            # Here we connect only modules represented in the analyzed set.
            if imported.get("level", 0) > 0:
                continue

            module = imported["module"]
            candidates = [module]

            candidates.extend(
                f"{module}.{name}"
                for name in imported.get("imported_names", [])
            )

            for candidate in candidates:
                target_file = module_to_file.get(candidate)

                if target_file and target_file != result["file"]:
                    edges.append({
                        "source": result["file"],
                        "target": target_file,
                        "type": "imports",
                        "module": candidate,
                        "line": imported["line"],
                    })
                    break

    return edges


def build_dependency_graph(
    repo_path: Path,
    target_files: list[str] | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_import_depth: int = DEFAULT_MAX_IMPORT_DEPTH,
) -> dict:
    """
    Build a bounded static dependency graph.

    Pass target_files from relevance search to analyze those files first.
    The default fallback is capped to avoid full-repository AST analysis.
    """

    repo_path = Path(repo_path).resolve()

    if not repo_path.is_dir():
        raise ValueError("Repository path does not exist.")

    all_python_files = get_python_files(repo_path)

    selected_files, discovered_count = _select_analysis_files(
        repo_path=repo_path,
        all_python_files=all_python_files,
        target_files=target_files,
        max_files=max_files,
        max_import_depth=max_import_depth,
    )

    file_results = [
        analyze_python_file(repo_path, file_path)
        for file_path in selected_files
    ]

    import_edges = resolve_internal_imports(file_results)

    function_index = defaultdict(list)

    for result in file_results:
        if not result["parse_success"]:
            continue

        for function in result["functions"]:
            function_index[function["name"]].append({
                "file": result["file"],
                "qualified_name": function["qualified_name"],
                "line": function["line"],
            })

    call_edges = []

    for result in file_results:
        if not result["parse_success"]:
            continue

        for call in result["calls"]:
            # Attribute calls such as obj.method are retained as evidence,
            # but only simple names are used for approximate resolution.
            lookup_name = call["name"].split(".")[-1]
            matches = function_index.get(lookup_name, [])

            call_edges.append({
                "source_file": result["file"],
                "source_scope": call["scope"],
                "target_name": call["name"],
                "target_candidates": matches[:10],
                "candidate_count": len(matches),
                "line": call["line"],
                "resolved": len(matches) == 1,
            })

    test_files = [
        result["file"]
        for result in file_results
        if (
            "test" in Path(result["file"]).parts
            or Path(result["file"]).name.startswith("test_")
        )
    ]

    return {
        "repository": str(repo_path),
        "files_discovered": discovered_count,
        "files_analyzed": len(file_results),
        "files_parsed": sum(
            result["parse_success"] for result in file_results
        ),
        "files_failed": sum(
            not result["parse_success"] for result in file_results
        ),
        "max_files": max_files,
        "max_import_depth": max_import_depth,
        "functions_found": sum(
            len(result["functions"]) for result in file_results
        ),
        "function_calls_found": sum(
            len(result["calls"]) for result in file_results
        ),
        "import_edges": import_edges,
        "call_edges": call_edges,
        "test_files": test_files,
        "file_details": file_results,
        "analysis_scope": (
            "targeted" if target_files else "bounded_fallback"
        ),
        "analysis_type": "static_ast_approximation",
    }
