
import re
from collections import Counter
from pathlib import Path

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

SEARCHABLE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".java", ".go", ".rs", ".cpp", ".c",
    ".h", ".php", ".rb", ".sql",
}

IGNORED_DIRS = {
    ".git", "node_modules", "venv", ".venv",
    "__pycache__", ".next", "dist", "build",
    ".idea", ".vscode", "vendor", ".tox",
    ".pytest_cache", ".mypy_cache", "coverage",
}

STOP_WORDS = {
    "the", "and", "for", "with", "from", "that",
    "this", "when", "then", "into", "does",
    "not", "are", "was", "were", "have",
    "has", "had", "but", "can", "could",
    "should", "would", "will", "there",
    "their", "about", "after", "before",
    "error", "issue", "bug", "problem",
    "code", "file", "files", "fails", "failed",
    "using", "use", "used", "expected",
    "actual", "instead", "return", "returns",
    "please", "make", "also", "some", "any",
    "which", "where", "what", "how", "why",
    "add", "added", "adding", "new", "work",
    "working", "works", "test", "tests",
    "testing", "implement", "implementation",
    "fix", "fixed", "fixing","these", "include", "both", "combinations",
    "ints", "floats", "also", "each",
    "their", "them", "they", "those",
}

# Important short technical identifiers.
# These must not be removed by normal length filtering.
TECHNICAL_TERMS = {
    "gt", "lt", "ge", "le", "eq", "ne",
    "id", "io", "ui", "db", "api", "os",
}

MAX_FILE_SIZE = 250_000
MAX_FILES = 10_000
MAX_MATCHES_PER_FILE = 3
MAX_KEYWORDS = 20


# --------------------------------------------------
# TASK INTENT DETECTION
# --------------------------------------------------

INTENT_PATTERNS = {
    "test": [
        "add test", "write test", "create test",
        "unit test", "test coverage",
        "regression test", "test cases",
        "missing tests",
    ],
    "bug_fix": [
        "fix bug", "does not work", "not working",
        "incorrect behavior", "unexpected behavior",
        "throws exception", "raises exception",
        "crashes", "broken", "fails to",
        "incorrect result",
    ],
    "validation": [
        "validation", "validate", "invalid input",
        "input validation", "parameter validation",
        "minlength", "maxlength", "constraint",
        "constraints", "type checking",
    ],
    "performance": [
        "slow", "performance", "memory leak",
        "optimization", "optimize", "latency",
        "bottleneck", "too much memory",
    ],
    "feature": [
        "add support", "implement feature",
        "new feature", "introduce support",
        "support for", "enable support",
    ],
}



def detect_task_intent(issue_text: str) -> str:
    """
    Detect the primary engineering task.

    Explicit user actions take priority over technical concepts.
    """

    text = issue_text.lower()

    # Priority 1: Explicit testing requests
    test_patterns = [
        r"\badd\s+tests?\b",
        r"\bwrite\s+tests?\b",
        r"\bcreate\s+tests?\b",
        r"\bunit\s+tests?\b",
        r"\bregression\s+tests?\b",
        r"\btest\s+coverage\b",
        r"\bmissing\s+tests?\b",
    ]

    if any(re.search(pattern, text) for pattern in test_patterns):
        return "test"

    # Priority 2: Explicit bug-fixing requests
    bug_patterns = [
        r"\bfix\s+(?:the\s+)?(?:bug|issue|error)\b",
        r"\bdoes not work\b",
        r"\bnot working\b",
        r"\bincorrect behavior\b",
        r"\bunexpected behavior\b",
        r"\bthrows exception\b",
        r"\braises exception\b",
        r"\bcrashes\b",
    ]

    if any(re.search(pattern, text) for pattern in bug_patterns):
        return "bug_fix"

    # Priority 3: Explicit feature requests
    feature_patterns = [
        r"\badd support\b",
        r"\bimplement feature\b",
        r"\bnew feature\b",
        r"\bintroduce support\b",
        r"\benable support\b",
    ]

    if any(re.search(pattern, text) for pattern in feature_patterns):
        return "feature"

    # Priority 4: Performance requests
    performance_patterns = [
        r"\bperformance\b",
        r"\boptimi[sz]",
        r"\bmemory leak\b",
        r"\blatency\b",
        r"\bbottleneck\b",
    ]

    if any(re.search(pattern, text) for pattern in performance_patterns):
        return "performance"

    # Priority 5: Technical validation concepts
    validation_patterns = [
        "validation",
        "validate",
        "invalid input",
        "input validation",
        "parameter validation",
        "minlength",
        "maxlength",
        "constraint",
        "constraints",
    ]

    if any(term in text for term in validation_patterns):
        return "validation"

    return "general"


# --------------------------------------------------
# KEYWORD EXTRACTION
# --------------------------------------------------

def extract_keywords(issue_text: str) -> list[str]:
    """
    Extract meaningful technical and natural-language terms.

    Preserves short technical identifiers such as gt, lt,
    ge and le.
    """

    words = re.findall(
        r"[a-zA-Z_][a-zA-Z0-9_]*",
        issue_text.lower()
    )

    counts = Counter()

    for word in words:

        if word in STOP_WORDS:
            continue

        if len(word) < 3 and word not in TECHNICAL_TERMS:
            continue

        counts[word] += 1

    return [
        word
        for word, _ in counts.most_common(MAX_KEYWORDS)
    ]


# --------------------------------------------------
# PATH CLASSIFICATION
# --------------------------------------------------

def classify_file_path(relative_path: str) -> str:
    """
    Classify a file as a test, implementation, or other file.
    """

    path = relative_path.lower()
    filename = Path(path).name

    test_indicators = {
        "test", "tests", "testing", "spec", "specs",
    }

    parts = set(Path(path).parts)

    if (
        parts.intersection(test_indicators)
        or filename.startswith("test_")
        or ".test." in filename
        or ".spec." in filename
    ):
        return "test"

    documentation_indicators = {
        "docs", "documentation", "examples",
    }

    if parts.intersection(documentation_indicators):
        return "documentation"

    return "implementation"


# --------------------------------------------------
# FILE SCORING
# --------------------------------------------------




def tokenize_identifier(value: str) -> set[str]:
    """
    Split snake_case, kebab-case and CamelCase identifiers
    into searchable components while preserving compounds.
    """

    # Separate acronym-to-word and lowercase-to-uppercase boundaries.
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)

    # Preserve complete identifiers and extract their components.
    tokens = set()

    for identifier in re.findall(r"[a-zA-Z0-9]+", value):
        token = identifier.lower()

        if token:
            tokens.add(token)

    return tokens




def calculate_file_score(
    relative_path: str,
    content: str,
    keywords: list[str],
    intent: str,
) -> dict:
    """
    Calculate a controlled relevance score.

    Scores are heuristic and explainable, not semantic AI scores.
    """

    path_lower = relative_path.lower()
    filename = Path(path_lower).stem

    # Extract individual tokens from the filename and path.
    filename_tokens = tokenize_identifier(filename)

    path_tokens = set()
    for part in Path(path_lower).parts:
        path_tokens.update(tokenize_identifier(part))


    file_type = classify_file_path(relative_path)

    score = 0
    matched_keywords = set()
    reasons = []
    matches = []

    
    # ----------------------------------------------
    # 1. PATH AND FILENAME MATCHING
    # ----------------------------------------------

    for keyword in keywords:

        if keyword in filename_tokens:
            score += 25
            matched_keywords.add(keyword)
            reasons.append(f"filename_match:{keyword}")

        elif keyword in path_tokens:
            score += 7
            matched_keywords.add(keyword)
            reasons.append(f"path_match:{keyword}")

    # Stronger signal when the filename directly
    # describes the requested concept.
    if intent == "test":
        if "path" in keywords and "path" in filename_tokens:
            score += 30
            reasons.append("filename_matches_test_subject")


    # ----------------------------------------------
    # 2. CONTENT MATCHING
    # ----------------------------------------------

    keyword_counts = Counter()

    for line_number, line in enumerate(
        content.splitlines(),
        start=1
    ):

        line_lower = line.lower()

        line_matches = [
            keyword
            for keyword in keywords
            if re.search(
                rf"(?<![a-zA-Z0-9_]){re.escape(keyword)}(?![a-zA-Z0-9_])",
                line_lower
            )
        ]

        if not line_matches:
            continue

        for keyword in line_matches:
            matched_keywords.add(keyword)

            # Limit contribution from repeated matches.
            keyword_counts[keyword] = min(
                keyword_counts[keyword] + 1,
                3
            )

        if len(matches) < MAX_MATCHES_PER_FILE:
            matches.append({
                "line": line_number,
                "snippet": line.strip()[:200],
                "keywords": line_matches,
            })

    # Each keyword contributes at most 6 content points.
    content_score = sum(
        min(count, 3) * 2
        for count in keyword_counts.values()
    )

    score += content_score

    if content_score:
        reasons.append("content_matches")

    # ----------------------------------------------
    # 3. DISTINCT KEYWORD COVERAGE
    # ----------------------------------------------

    coverage_score = len(matched_keywords) * 3
    score += coverage_score

    if matched_keywords:
        reasons.append("multiple_keyword_matches")

    # ----------------------------------------------
    # 4. TASK-AWARE FILE PRIORITY
    # ----------------------------------------------

    if intent == "test":

        if file_type == "test":
            score += 15
            reasons.append("test_file_for_test_task")

    elif intent in {
        "bug_fix", "validation", "feature", "performance",
    }:

        if file_type == "implementation":
            score += 8
            reasons.append("implementation_file_for_code_task")

        elif file_type == "test":
            score += 2
            reasons.append("test_file_may_verify_behavior")

    return {
        "file": relative_path,
        "score": score,
        "file_type": file_type,
        "matched_keywords": sorted(matched_keywords),
        "reasons": sorted(set(reasons)),
        "matches": matches,
    }


# --------------------------------------------------
# MAIN SEARCH FUNCTION
# --------------------------------------------------

def search_relevant_files(
    repo_path: Path,
    issue_text: str,
    top_k: int = 10,
) -> dict:
    """
    Rank source files by explainable relevance.

    Repository code is read but never executed.
    """

    repo_path = Path(repo_path).resolve()

    keywords = extract_keywords(issue_text)
    intent = detect_task_intent(issue_text)

    if not keywords:
        return {
            "keywords": [],
            "task_intent": intent,
            "files_scanned": 0,
            "files_skipped": 0,
            "results": [],
        }

    # Use a dictionary to prevent duplicate file results.
    results_by_path = {}

    files_scanned = 0
    files_skipped = 0

    for path in repo_path.rglob("*"):

        try:
            relative = path.relative_to(repo_path)
        except ValueError:
            continue

        # Skip ignored directories.
        if any(
            part.lower() in IGNORED_DIRS
            for part in relative.parts
        ):
            continue

        if not path.is_file() or path.is_symlink():
            continue

        if path.suffix.lower() not in SEARCHABLE_EXTENSIONS:
            continue

        try:
            if path.stat().st_size > MAX_FILE_SIZE:
                files_skipped += 1
                continue

            content = path.read_text(
                encoding="utf-8",
                errors="replace"
            )

        except (OSError, UnicodeError):
            files_skipped += 1
            continue

        files_scanned += 1

        relative_path = relative.as_posix()

        result = calculate_file_score(
            relative_path=relative_path,
            content=content,
            keywords=keywords,
            intent=intent,
        )

        if result["score"] > 0:
            results_by_path[relative_path] = result

        if files_scanned >= MAX_FILES:
            break

    results = list(results_by_path.values())

    results.sort(
        key=lambda item: (
            -item["score"],
            -len(item["matched_keywords"]),
            item["file"],
        )
    )

    return {
        "keywords": keywords,
        "task_intent": intent,
        "files_scanned": files_scanned,
        "files_skipped": files_skipped,
        "results": results[:max(1, top_k)],
    }
