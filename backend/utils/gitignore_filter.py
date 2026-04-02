"""Gitignore-style file filtering for repository uploads."""

import logging
from typing import List, Optional, Tuple

import pathspec

logger = logging.getLogger(__name__)

DEFAULT_IGNORE_PATTERNS = [
    "node_modules/",
    "__pycache__/",
    ".git/",
    ".svn/",
    ".hg/",
    "dist/",
    "build/",
    ".next/",
    ".nuxt/",
    "target/",
    "vendor/",
    "*.pyc",
    "*.pyo",
    "*.class",
    "*.o",
    "*.so",
    "*.dylib",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.lock",
    "package-lock.json",
    ".DS_Store",
    "Thumbs.db",
    ".env",
    ".env.*",
]


class GitignoreFilter:
    """Filter files using gitignore-style patterns.

    Combines a hardcoded default ignore list with optional .gitignore content
    and additional user-supplied patterns.
    """

    def __init__(
        self,
        gitignore_content: Optional[str] = None,
        additional_patterns: Optional[List[str]] = None,
    ):
        all_patterns = list(DEFAULT_IGNORE_PATTERNS)
        if gitignore_content:
            for line in gitignore_content.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    all_patterns.append(line)
        if additional_patterns:
            all_patterns.extend(additional_patterns)

        self._spec = pathspec.PathSpec.from_lines("gitignore", all_patterns)

    def should_ignore(self, file_path: str) -> bool:
        """Return True if the file should be ignored."""
        return self._spec.match_file(file_path)

    def filter_file_list(self, file_paths: List[str]) -> Tuple[List[str], List[str]]:
        """Split file list into (kept, ignored)."""
        kept = []
        ignored = []
        for path in file_paths:
            if self.should_ignore(path):
                ignored.append(path)
            else:
                kept.append(path)
        return kept, ignored
