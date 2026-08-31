"""Idempotent data the extension needs on first run — reference rows, default
settings. Rule and lesson bundles are applied with `flask load-rules
bundles/rules.json --enable` and `flask load-lessons bundles/lessons.json`.
Runs once per extension version (a stamp under data/extensions/), but must be
safe to run again."""


def seed(app):
    return {"seeded": 0}
