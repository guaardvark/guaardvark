#!/usr/bin/env python3
"""Validation helpers for agent recipes and screen-control knowledge."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List


SUPPORTED_RECIPE_ACTIONS = {
    "click",
    "hotkey",
    "type",
    "wait",
    "wait_until_settled",
    "wait_until_visible",
}


@dataclass
class ValidationIssue:
    path: str
    message: str
    severity: str = "error"


@dataclass
class ValidationResult:
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def add(self, path: str, message: str, severity: str = "error") -> None:
        self.issues.append(ValidationIssue(path=path, message=message, severity=severity))

    def error_messages(self) -> List[str]:
        return [f"{i.path}: {i.message}" for i in self.issues if i.severity == "error"]


def _placeholders(text: str) -> Iterable[int]:
    for match in re.finditer(r"\{(\d+)\}", text or ""):
        yield int(match.group(1))


def validate_recipe(name: str, recipe: Dict[str, Any]) -> ValidationResult:
    """Validate one recipes.json entry against LEARNING_PRINCIPLES.md."""
    result = ValidationResult()
    if not isinstance(recipe, dict):
        result.add(name, "recipe must be an object")
        return result

    triggers = recipe.get("triggers") or []
    steps = recipe.get("steps") or []
    if not isinstance(triggers, list) or not triggers:
        result.add(f"{name}.triggers", "must be a non-empty list")
    if not isinstance(steps, list) or not steps:
        result.add(f"{name}.steps", "must be a non-empty list")

    max_capture_group = 0
    for idx, pattern in enumerate(triggers):
        path = f"{name}.triggers[{idx}]"
        if not isinstance(pattern, str):
            result.add(path, "trigger must be a string")
            continue
        if not pattern.startswith("^"):
            result.add(path, "trigger must be anchored with ^")
        if not pattern.endswith("$") and not pattern.endswith("\\s*$"):
            result.add(path, "trigger must be anchored at the end")
        try:
            compiled = re.compile(pattern)
            max_capture_group = max(max_capture_group, compiled.groups)
        except re.error as e:
            result.add(path, f"invalid regex: {e}")

    nontrivial_actions = 0
    for idx, step in enumerate(steps):
        path = f"{name}.steps[{idx}]"
        if not isinstance(step, dict):
            result.add(path, "step must be an object")
            continue
        action = step.get("action")
        if action not in SUPPORTED_RECIPE_ACTIONS:
            result.add(path, f"unsupported action {action!r}")
            continue
        if action != "wait":
            nontrivial_actions += 1

        if action == "click":
            if "x" in step or "y" in step:
                result.add(path, "click steps must not store x/y coordinates")
            target = (step.get("target_description") or "").strip()
            if not target:
                result.add(path, "click step needs target_description")
            words = re.findall(r"[A-Za-z0-9_]+", target)
            if len(words) > 6:
                result.add(path, "target_description must be <= 6 words")

        if action == "wait":
            result.add(path, "legacy wait step should migrate to wait_until_*", severity="warning")

        for key, value in step.items():
            if isinstance(value, str):
                for group_idx in _placeholders(value):
                    if group_idx > max_capture_group:
                        result.add(path, f"placeholder {{{group_idx}}} has no matching trigger capture")
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        for group_idx in _placeholders(item):
                            if group_idx > max_capture_group:
                                result.add(path, f"placeholder {{{group_idx}}} has no matching trigger capture")

    if nontrivial_actions > 1 and not recipe.get("success_proof"):
        result.add(f"{name}.success_proof", "nontrivial recipes need a final vision-readable proof")

    return result


def validate_recipe_library(recipes: Dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    for name, recipe in (recipes or {}).items():
        if str(name).startswith("_"):
            continue
        child = validate_recipe(str(name), recipe)
        result.issues.extend(child.issues)
    return result
