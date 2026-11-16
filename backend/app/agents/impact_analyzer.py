
from pathlib import Path
from collections import defaultdict

from app.repositories.search import search_relevant_files
from app.agents.context_builder import build_code_context
from app.agents.dependency_analyzer import build_dependency_graph


MAX_DEPENDENCY_FILES = 120
MAX_IMPORT_DEPTH = 1


def _get_search_results(search_result):
    """Support the search result formats used by Forge."""

    if isinstance(search_result, list):
        return search_result

    if isinstance(search_result, dict):
        for key in ("results", "ranked_files", "files"):
            value = search_result.get(key)

            if isinstance(value, list):
                return value

    return []


def _get_file_path(result):
    """Extract a repository-relative path from a search result."""

    if not isinstance(result, dict):
        return None

    path = (
        result.get("file")
        or result.get("path")
        or result.get("relative_path")
    )

    if not isinstance(path, str) or not path.strip():
        return None

    return path.replace("\\", "/").lstrip("./")


def _extract_functions(file_context):
    """Extract function information from available context structures."""

    if not isinstance(file_context, dict):
        return []

    functions = file_context.get("functions")

    if isinstance(functions, list) and functions:
        return functions

    structure = file_context.get("structure", {})

    if isinstance(structure, dict):
        structured_functions = structure.get("functions", [])

        if isinstance(structured_functions, list):
            return structured_functions

    symbols = file_context.get("symbols", [])

    if isinstance(symbols, dict):
        symbols = symbols.get("items", [])

    if isinstance(symbols, list):
        return [
            symbol
            for symbol in symbols
            if isinstance(symbol, dict)
            and symbol.get("type", symbol.get("kind", "")).lower()
            in {"function", "async_function", "method"}
        ]

    return []


def _get_evidence_snippets(file_context):
    """Return snippets only when they have the expected list format."""

    snippets = file_context.get("evidence_snippets", [])

    return snippets if isinstance(snippets, list) else []


def analyze_impact(
    repo_path: Path,
    issue_text: str,
    top_k: int = 10,
):
    """
    Run targeted search, context extraction and dependency analysis
    to estimate the potential impact of an issue.

    Dependency analysis is bounded to relevant Python files and
    their directly imported internal modules.
    """

    repo_path = Path(repo_path).resolve()

    if not repo_path.is_dir():
        raise ValueError("Repository path does not exist.")

    if not issue_text or len(issue_text.strip()) < 3:
        raise ValueError(
            "Issue description must contain at least 3 characters."
        )

    # 1. Find relevant files.
    search_result = search_relevant_files(
        repo_path,
        issue_text,
        top_k=top_k,
    )

    search_files = _get_search_results(search_result)

    # Preserve search ranking order while removing duplicate paths.
    relevant_paths = []
    seen_paths = set()

    for result in search_files:
        path = _get_file_path(result)

        if path and path not in seen_paths:
            relevant_paths.append(path)
            seen_paths.add(path)

    relevant_path_set = set(relevant_paths)

    # 2. Extract code context.
    context = build_code_context(
        repo_path,
        search_result,
    )

    if not isinstance(context, dict):
        context = {}

    context_files = context.get("files", [])

    if not isinstance(context_files, list):
        context_files = []

    context_by_path = {
        _get_file_path(item): item
        for item in context_files
        if isinstance(item, dict) and _get_file_path(item)
    }

    # 3. Build a bounded dependency graph using relevant search results.
    dependency_graph = build_dependency_graph(
        repo_path,
        target_files=relevant_paths,
        max_files=MAX_DEPENDENCY_FILES,
        max_import_depth=MAX_IMPORT_DEPTH,
    )

    # 4. Identify dependencies connected to relevant files.
    connected_files = defaultdict(list)

    for edge in dependency_graph.get("import_edges", []):
        source = edge["source"]
        target = edge["target"]

        if source in relevant_path_set:
            connected_files[source].append({
                "file": target,
                "relationship": "imports",
                "line": edge.get("line"),
                "module": edge.get("module"),
            })

        if target in relevant_path_set:
            connected_files[target].append({
                "file": source,
                "relationship": "imported_by",
                "line": edge.get("line"),
                "module": edge.get("module"),
            })

    # 5. Identify syntactic function calls from relevant files.
    related_calls = []

    for edge in dependency_graph.get("call_edges", []):
        if edge["source_file"] not in relevant_path_set:
            continue

        related_calls.append({
            "source_file": edge["source_file"],
            "source_scope": edge["source_scope"],
            "target_name": edge["target_name"],
            "target_candidates": edge.get("target_candidates", []),
            "candidate_count": edge.get("candidate_count", 0),
            "line": edge["line"],
            "resolved": edge["resolved"],
        })

    # 6. Identify potentially affected tests.
    test_files = set(dependency_graph.get("test_files", []))
    potentially_affected_tests = set()

    for path in relevant_path_set:
        if path in test_files:
            potentially_affected_tests.add(path)

        for dependency in connected_files[path]:
            candidate = dependency["file"]

            if candidate in test_files:
                potentially_affected_tests.add(candidate)

    for file_data in context_files:
        if not isinstance(file_data, dict):
            continue

        path = _get_file_path(file_data)

        if path in test_files:
            potentially_affected_tests.add(path)

    # 7. Build evidence-based impact report.
    impacted_files = []

    for path in relevant_paths:
        file_context = context_by_path.get(path, {})

        search_entry = next(
            (
                item for item in search_files
                if _get_file_path(item) == path
            ),
            {},
        )

        impacted_files.append({
            "file": path,
            "impact_source": "repository_search",
            "score": (
                search_entry.get("score")
                if isinstance(search_entry, dict)
                else None
            ),
            "functions": _extract_functions(file_context),
            "evidence_snippets": _get_evidence_snippets(file_context),
            "dependencies": connected_files[path],
        })

    import_edges = dependency_graph.get("import_edges", [])
    call_edges = dependency_graph.get("call_edges", [])

    return {
        "issue_text": issue_text,
        "repository": str(repo_path),

        "summary": {
            "relevant_files": len(relevant_path_set),
            "context_files_analyzed": context.get(
                "files_analyzed", len(context_files)
            ),
            "potentially_affected_tests": len(
                potentially_affected_tests
            ),
            "related_function_calls": len(related_calls),
            "dependency_relationships": sum(
                len(items) for items in connected_files.values()
            ),
        },

        "impacted_files": impacted_files,

        "potentially_affected_tests": sorted(
            potentially_affected_tests
        ),

        "related_function_calls": related_calls,

        "context": context,

        "dependency_graph_summary": {
            "files_discovered": dependency_graph.get(
                "files_discovered", 0
            ),
            "files_analyzed": dependency_graph.get(
                "files_analyzed", 0
            ),
            "files_parsed": dependency_graph.get(
                "files_parsed", 0
            ),
            "files_failed": dependency_graph.get(
                "files_failed", 0
            ),
            "max_files": dependency_graph.get(
                "max_files", MAX_DEPENDENCY_FILES
            ),
            "max_import_depth": dependency_graph.get(
                "max_import_depth", MAX_IMPORT_DEPTH
            ),
            "functions_found": dependency_graph.get(
                "functions_found", 0
            ),
            "function_calls_found": dependency_graph.get(
                "function_calls_found", 0
            ),
            "import_edges": len(import_edges),
            "call_edges": len(call_edges),
            "analysis_scope": dependency_graph.get(
                "analysis_scope", "unknown"
            ),
        },

        "analysis_type": "static_evidence_based_impact_analysis",
        "execution_performed": False,
        "root_cause_confirmed": False,
    }
