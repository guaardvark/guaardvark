"""Extract symbols (functions, classes, imports) from source code using regex.

Uses regex patterns for broad language coverage. Precise enough for metadata
indexing — not intended as a full language server.
"""

import logging
import re
from typing import Dict, List

logger = logging.getLogger(__name__)

# Regex patterns per language for symbol extraction
_PATTERNS = {
    "python": {
        "function": re.compile(r"^def\s+(\w+)\s*\(", re.MULTILINE),
        "class": re.compile(r"^class\s+(\w+)", re.MULTILINE),
        "method": re.compile(r"^\s+def\s+(\w+)\s*\(", re.MULTILINE),
        "import": re.compile(
            r"^(?:from\s+\S+\s+import\s+(.+)|import\s+(.+))$", re.MULTILINE
        ),
    },
    "javascript": {
        "function": re.compile(
            r"(?:^|\s)function\s+(\w+)\s*\(", re.MULTILINE
        ),
        "const_function": re.compile(
            r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[a-zA-Z_]\w*)\s*=>",
            re.MULTILINE,
        ),
        "class": re.compile(r"^class\s+(\w+)", re.MULTILINE),
        "import": re.compile(
            r"import\s+(?:\{[^}]+\}|(\w+))\s+from\s+['\"]([^'\"]+)['\"]",
            re.MULTILINE,
        ),
    },
    "typescript": None,  # Shares javascript patterns
    "java": {
        "class": re.compile(
            r"(?:public|private|protected)?\s*class\s+(\w+)", re.MULTILINE
        ),
        "function": re.compile(
            r"(?:public|private|protected)\s+\w+\s+(\w+)\s*\(", re.MULTILINE
        ),
        "import": re.compile(r"^import\s+(.+);", re.MULTILINE),
    },
    "go": {
        "function": re.compile(r"^func\s+(\w+)\s*\(", re.MULTILINE),
        "method": re.compile(r"^func\s+\([^)]+\)\s+(\w+)\s*\(", re.MULTILINE),
        "class": re.compile(r"^type\s+(\w+)\s+struct", re.MULTILINE),
        "import": re.compile(r'"([^"]+)"', re.MULTILINE),
    },
    "rust": {
        "function": re.compile(r"^(?:pub\s+)?fn\s+(\w+)", re.MULTILINE),
        "class": re.compile(r"^(?:pub\s+)?struct\s+(\w+)", re.MULTILINE),
        "import": re.compile(r"^use\s+(.+);", re.MULTILINE),
    },
    "ruby": {
        "function": re.compile(r"^\s*def\s+(\w+)", re.MULTILINE),
        "class": re.compile(r"^class\s+(\w+)", re.MULTILINE),
        "import": re.compile(r"^require\s+['\"](.+)['\"]", re.MULTILINE),
    },
    "php": {
        "function": re.compile(r"function\s+(\w+)\s*\(", re.MULTILINE),
        "class": re.compile(r"class\s+(\w+)", re.MULTILINE),
        "import": re.compile(r"^use\s+(.+);", re.MULTILINE),
    },
    "c": {
        "function": re.compile(
            r"^\w[\w\s\*]+\s+(\w+)\s*\([^)]*\)\s*\{", re.MULTILINE
        ),
        "class": re.compile(r"^(?:typedef\s+)?struct\s+(\w+)", re.MULTILINE),
        "import": re.compile(r'^#include\s+[<"]([^>"]+)[>"]', re.MULTILINE),
    },
    "cpp": None,  # Shares c patterns
}

# Alias mappings
_PATTERNS["typescript"] = _PATTERNS["javascript"]
_PATTERNS["cpp"] = _PATTERNS["c"]
_PATTERNS["c_sharp"] = _PATTERNS["java"]  # Close enough for extraction
_PATTERNS["swift"] = _PATTERNS["java"]
_PATTERNS["kotlin"] = _PATTERNS["java"]
_PATTERNS["scala"] = _PATTERNS["java"]


def extract_symbols(code: str, language: str) -> List[Dict]:
    """Extract symbols from source code.

    Returns a list of dicts with keys: name, type, line (1-indexed).
    """
    if not code or not code.strip():
        return []

    patterns = _PATTERNS.get(language)
    if patterns is None:
        return []

    symbols = []

    for symbol_type, pattern in patterns.items():
        # Normalize const_function to function
        output_type = "function" if symbol_type == "const_function" else symbol_type

        for match in pattern.finditer(code):
            # Get all non-None groups
            groups = [g for g in match.groups() if g is not None]
            if not groups:
                continue

            raw_name = groups[0].strip()

            # For imports, may have comma-separated names
            if output_type == "import":
                for name in re.split(r"\s*,\s*", raw_name):
                    name = name.strip().split(" as ")[0].strip()
                    name = name.split(".")[-1].strip()
                    if name and name not in ("*",):
                        line_num = code[:match.start()].count("\n") + 1
                        symbols.append({
                            "name": name,
                            "type": "import",
                            "line": line_num,
                        })
            else:
                line_num = code[:match.start()].count("\n") + 1
                symbols.append({
                    "name": raw_name,
                    "type": output_type,
                    "line": line_num,
                })

    return symbols
