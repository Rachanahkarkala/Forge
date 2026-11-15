
import re
from pathlib import Path

from app.repositories.search import (
    detect_task_intent,
    extract_keywords,
)


def extract_issue_text(issue: dict) -> str:
    """Combine the GitHub issue title and description."""

    title = issue.get("title") or ""
    description = issue.get("description") or issue.get("body") or ""

    return f"{title}\n\n{description}".strip()


def identify_expected_outcomes(
    issue_text: str,
    intent: str,
) -> list[str]:
    """Identify outcomes explicitly requested in the issue."""

    text = issue_text.lower()
    outcomes = []

    if intent == "test":
        outcomes.append(
            "Identify missing test scenarios and verify expected behavior."
        )

    if intent == "bug_fix":
        outcomes.append(
            "Reproduce the reported incorrect behavior and verify a fix."
        )

    if intent == "feature":
        outcomes.append(
            "Identify the required functionality and verify its implementation."
        )

    if intent == "validation":
        outcomes.append(
            "Inspect input validation rules and boundary conditions."
        )

    if intent == "performance":
        outcomes.append(
            "Identify performance bottlenecks and establish measurable evidence."
        )

    if re.search(r"\b(integer|int|float|decimal|numeric)\b", text):
        outcomes.append(
            "Consider relevant numeric types and boundary values."
        )

    if not outcomes:
        outcomes.append(
            "Investigate the reported request and determine the expected behavior."
        )

    return outcomes


def build_investigation_steps(
    intent: str,
    relevant_files: list[dict],
) -> list[dict]:
    """Generate an ordered investigation plan."""

    steps = [
        {
            "step": 1,
            "action": "Understand the reported requirement",
            "details": (
                "Review the issue description and identify the "
                "expected and actual behavior."
            ),
        },
        {
            "step": 2,
            "action": "Inspect relevant repository files",
            "details": (
                "Examine the highest-ranked files and trace "
                "their relationships."
            ),
        },
    ]

    if intent == "test":
        steps.extend([
            {
                "step": 3,
                "action": "Review existing test coverage",
                "details": (
                    "Identify existing test cases, missing scenarios "
                    "and boundary conditions."
                ),
            },
            {
                "step": 4,
                "action": "Prepare a test strategy",
                "details": (
                    "Define test cases that demonstrate the "
                    "requested behavior."
                ),
            },
        ])

    elif intent == "bug_fix":
        steps.extend([
            {
                "step": 3,
                "action": "Trace the suspected execution path",
                "details": (
                    "Follow relevant functions and identify "
                    "potential failure points."
                ),
            },
            {
                "step": 4,
                "action": "Plan bug reproduction",
                "details": (
                    "Determine the inputs and conditions needed "
                    "to reproduce the reported behavior."
                ),
            },
        ])

    elif intent == "feature":
        steps.extend([
            {
                "step": 3,
                "action": "Inspect existing architecture",
                "details": (
                    "Identify existing components and extension points."
                ),
            },
            {
                "step": 4,
                "action": "Plan the implementation",
                "details": (
                    "Determine which components may need modification."
                ),
            },
        ])

    else:
        steps.extend([
            {
                "step": 3,
                "action": "Investigate relevant behavior",
                "details": (
                    "Examine implementation details and existing tests."
                ),
            },
            {
                "step": 4,
                "action": "Determine verification requirements",
                "details": (
                    "Identify how the requested change can be verified."
                ),
            },
        ])

    steps.append({
        "step": 5,
        "action": "Summarize findings",
        "details": (
            "Record evidence, unresolved questions and recommended "
            "next actions. Do not assume a root cause without evidence."
        ),
    })

    return steps


def build_investigation_plan(
    issue: dict,
    search_result: dict | None = None,
) -> dict:
    """
    Convert a GitHub issue and repository search results
    into a structured investigation plan.

    This stage does not modify or execute repository code.
    """

    issue_text = extract_issue_text(issue)

    if not issue_text:
        raise ValueError("Issue title and description are empty.")

    intent = detect_task_intent(issue_text)
    keywords = extract_keywords(issue_text)

    search_result = search_result or {}
    ranked_results = search_result.get("results", [])

    relevant_files = [
        {
            "file": item["file"],
            "score": item["score"],
            "matched_keywords": item.get("matched_keywords", []),
            "reasons": item.get("reasons", []),
        }
        for item in ranked_results[:10]
    ]

    outcomes = identify_expected_outcomes(issue_text, intent)

    steps = build_investigation_steps(
        intent,
        relevant_files,
    )

    return {
        "issue": {
            "title": issue.get("title"),
            "issue_number": issue.get("issue_number"),
            "issue_url": issue.get("issue_url"),
        },
        "understanding": {
            "task_intent": intent,
            "keywords": keywords,
            "issue_summary": issue_text[:1000],
            "expected_outcomes": outcomes,
        },
        "repository_context": {
            "files_considered": len(relevant_files),
            "relevant_files": relevant_files,
        },
        "investigation_plan": steps,
        "execution_status": "planning_only",
        "root_cause": None,
    }
