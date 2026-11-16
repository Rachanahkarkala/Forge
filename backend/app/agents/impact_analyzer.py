
from pathlib import Path
from collections import defaultdict

from app.repositories.search import search_relevant_files
from app.agents.context_builder import build_code_context
from app.agents.dependency_analyzer import build_dependency_graph


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

    return (
        result.get("file")
        or result.get("path")
        or result.get("relative_path")
    )


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


def analyze_impact(
    repo_path: Path,
    issue_text: str,
    top_k: int = 10,
):
    """
    Run Forge's search, context extraction and dependency analysis
    to estimate the potential impact of an issue.
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

    relevant_paths = {
        path
        for result in search_files
        if (path := _get_file_path(result))
    }

    # 2. Extract code context.
    context = build_code_context(
        repo_path,
        search_result,
    )

    context_files = context.get("files", [])

    context_by_path = {
        item.get("file"): item
        for item in context_files
        if isinstance(item, dict) and item.get("file")
    }

    # 3. Build repository dependency graph.
    dependency_graph = build_dependency_graph(repo_path)

    # 4. Identify dependencies connected to relevant files.
    connected_files = defaultdict(list)

    for edge in dependency_graph["import_edges"]:
        source = edge["source"]
        target = edge["target"]

        if source in relevant_paths:
            connected_files[source].append({
                "file": target,
                "relationship": "imports",
            })

        if target in relevant_paths:
            connected_files[target].append({
                "file": source,
                "relationship": "imported_by",
            })

    # 5. Identify function calls associated with relevant files.
    related_calls = []

    for edge in dependency_graph["call_edges"]:
        if edge["source_file"] in relevant_paths:
            related_calls.append({
                "source_file": edge["source_file"],
                "source_scope": edge["source_scope"],
                "target_name": edge["target_name"],
                "target_candidates": edge["target_candidates"],
                "line": edge["line"],
                "resolved": edge["resolved"],
            })

    # 6. Identify potentially affected tests.
    test_files = set(dependency_graph["test_files"])
    potentially_affected_tests = set()

    for path in relevant_paths:
        if path in test_files:
            potentially_affected_tests.add(path)

        for dependency in connected_files[path]:
            candidate = dependency["file"]

            if candidate in test_files:
                potentially_affected_tests.add(candidate)

    for file_data in context_files:
        path = file_data.get("file")

        if path in test_files:
            potentially_affected_tests.add(path)

    # 7. Build evidence-based impact report.
    impacted_files = []

    for path in sorted(relevant_paths):
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
            "score": search_entry.get("score"),
            "functions": _extract_functions(file_context),
            "evidence_snippets": file_context.get(
                "evidence_snippets", []
            ),
            "dependencies": connected_files[path],
        })

    return {
        "issue_text": issue_text,
        "repository": str(repo_path),

        "summary": {
            "relevant_files": len(relevant_paths),
            "context_files_analyzed": context.get(
                "files_analyzed", 0
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
            "files_analyzed": dependency_graph["files_analyzed"],
            "functions_found": dependency_graph["functions_found"],
            "function_calls_found": dependency_graph[
                "function_calls_found"
            ],
            "import_edges": len(
                dependency_graph["import_edges"]
            ),
        },

        "analysis_type": "static_evidence_based_impact_analysis",
        "execution_performed": False,
        "root_cause_confirmed": False,
    }
