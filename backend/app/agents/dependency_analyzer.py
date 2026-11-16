
import ast
from pathlib import Path
from collections import defaultdict


IGNORED_DIRS = {
    ".git",
    "venv",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}

MAX_FILE_SIZE = 250_000


def get_python_files(repo_path: Path):
    """Collect Python files without entering ignored directories."""

    python_files = []

    for path in repo_path.rglob("*.py"):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue

        if path.is_symlink() or not path.is_file():
            continue

        try:
            if path.stat().st_size > MAX_FILE_SIZE:
                continue
        except OSError:
            continue

        python_files.append(path)

    return python_files


def get_module_name(repo_path: Path, file_path: Path):
    """Convert a Python file path into an approximate module name."""

    relative = file_path.relative_to(repo_path)

    parts = list(relative.with_suffix("").parts)

    if parts[-1] == "__init__":
        parts.pop()

    return ".".join(parts)


def extract_imports(tree):
    """Extract imports and their referenced module names."""

    imports = []

    for node in ast.walk(tree):

        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({
                    "module": alias.name,
                    "imported_names": [],
                    "line": node.lineno,
                })

        elif isinstance(node, ast.ImportFrom):
            imports.append({
                "module": node.module or "",
                "imported_names": [
                    alias.name for alias in node.names
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


def analyze_python_file(repo_path: Path, file_path: Path):
    """Extract static dependency information from one Python file."""

    try:
        source = file_path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        tree = ast.parse(source, filename=str(file_path))

        visitor = DependencyVisitor()
        visitor.visit(tree)

        return {
            "file": file_path.relative_to(repo_path).as_posix(),
            "module": get_module_name(repo_path, file_path),
            "imports": extract_imports(tree),
            "functions": visitor.functions,
            "calls": visitor.calls,
            "parse_success": True,
            "error": None,
        }

    except (SyntaxError, OSError, ValueError) as exc:
        return {
            "file": file_path.relative_to(repo_path).as_posix(),
            "module": get_module_name(repo_path, file_path),
            "imports": [],
            "functions": [],
            "calls": [],
            "parse_success": False,
            "error": str(exc),
        }


def resolve_internal_imports(file_results):
    """Connect imports to modules found inside this repository."""

    module_to_file = {
        result["module"]: result["file"]
        for result in file_results
        if result["parse_success"]
    }

    edges = []

    for result in file_results:
        if not result["parse_success"]:
            continue

        for imported in result["imports"]:
            module = imported["module"]

            candidates = []

            if module:
                candidates.append(module)

            # Relative imports are recorded but not fully resolved here.
            if imported.get("level", 0) > 0:
                continue

            for candidate in candidates:
                if candidate in module_to_file:
                    edges.append({
                        "source": result["file"],
                        "target": module_to_file[candidate],
                        "type": "imports",
                        "module": candidate,
                        "line": imported["line"],
                    })

    return edges


def build_dependency_graph(repo_path: Path):
    """Build a static dependency graph for a Python repository."""

    repo_path = Path(repo_path).resolve()

    if not repo_path.is_dir():
        raise ValueError("Repository path does not exist.")

    python_files = get_python_files(repo_path)

    file_results = [
        analyze_python_file(repo_path, file_path)
        for file_path in python_files
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
            matches = function_index.get(call["name"], [])

            call_edges.append({
                "source_file": result["file"],
                "source_scope": call["scope"],
                "target_name": call["name"],
                "target_candidates": matches,
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
        "files_analyzed": len(file_results),
        "files_parsed": sum(
            result["parse_success"] for result in file_results
        ),
        "files_failed": sum(
            not result["parse_success"] for result in file_results
        ),
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
        "analysis_type": "static_ast_approximation",
    }
