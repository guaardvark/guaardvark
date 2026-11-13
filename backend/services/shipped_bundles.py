"""Apply the shipped rule and lesson bundles at startup, without overriding a person.

`flask load-rules` and `flask load-lessons` apply a bundle by hand and overwrite
whatever is there. A stock install never ran them, so its database had no
persona, no rules and no lessons. At each start this module applies every
bundle in backend/rule_bundles/ and backend/lesson_bundles/ under three rules:

- An entry the database has never had is added.
- An entry still exactly as a bundle last left it is brought up to date when
  the bundle changes.
- An entry a person edited, switched off or deleted is left as they left it.

What was applied is remembered per entry (a fingerprint of the fields a person
can change) in the setting ``shipped_bundles_applied``. The Rules switch in
Settings is not touched: rules still reach chat only when it is on.
"""

import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1]
RULE_BUNDLE_DIR = BACKEND_DIR / "rule_bundles"
LESSON_BUNDLE_DIR = BACKEND_DIR / "lesson_bundles"
RECORD_KEY = "shipped_bundles_applied"


def _fingerprint(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _rule_fingerprint(rule_text, is_active, target_models_json) -> str:
    return _fingerprint(rule_text, bool(is_active), target_models_json or '["__ALL__"]')


def _lesson_fingerprint(steps, parameters) -> str:
    return _fingerprint(steps or [], parameters or [])


def _lesson_payload(row) -> dict:
    try:
        decoded = json.loads(row.content or "")
        if isinstance(decoded, dict):
            return decoded
    except (json.JSONDecodeError, TypeError):
        pass
    extra = row.extra_data if isinstance(getattr(row, "extra_data", None), dict) else {}
    return extra.get("lesson") if isinstance(extra.get("lesson"), dict) else {}


def _load_record(db, Setting) -> dict:
    row = db.session.get(Setting, RECORD_KEY)
    try:
        return json.loads(row.value) if row and row.value else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _save_record(db, Setting, record: dict) -> None:
    row = db.session.get(Setting, RECORD_KEY)
    value = json.dumps(record, sort_keys=True)
    if row is None:
        db.session.add(Setting(key=RECORD_KEY, value=value))
    else:
        row.value = value


def _apply_rules(path: Path, applied: dict, counts: dict) -> None:
    from backend.models import Rule, db

    data = json.loads(path.read_text())
    for entry in data.get("rules", []):
        name = entry["name"]
        level = entry.get("level", "SYSTEM")
        rule_type = entry.get("type", "PROMPT_TEMPLATE")
        key = f"{name}|{level}|{rule_type}"
        targets = entry.get("target_models_json", '["__ALL__"]')
        active = bool(entry.get("is_active", True))
        shipped = _rule_fingerprint(entry["rule_text"], active, targets)
        last = applied.get(key)

        rows = Rule.query.filter_by(name=name).all()
        row = next((r for r in rows if r.level == level and r.type == rule_type), None)

        if row is None:
            # Deleted after we added it, or a person keeps their own rule of
            # that name under another level or type.
            if last is not None or any(r.is_active for r in rows):
                counts["kept"] += 1
                continue
            row = Rule(name=name, level=level, type=rule_type, rule_text=entry["rule_text"])
            db.session.add(row)
            counts["added"] += 1
        else:
            current = _rule_fingerprint(row.rule_text, row.is_active, row.target_models_json)
            if current == shipped:
                applied[key] = shipped
                continue
            if last is None or current != last:
                counts["kept"] += 1
                continue
            counts["updated"] += 1

        row.rule_text = entry["rule_text"]
        row.description = entry.get("description", "")
        row.command_label = entry.get("command_label")
        row.output_schema_name = entry.get("output_schema_name")
        row.target_models_json = targets
        row.is_active = active
        # The active-prompt cache is keyed on the newest updated_at.
        row.updated_at = datetime.now()
        applied[key] = shipped


def _apply_lessons(path: Path, applied: dict, counts: dict) -> None:
    from backend.api.memory_api import add_memory
    from backend.models import AgentMemory, db
    from backend.services.memory_contract import coerce_importance, normalize_tags, validate_lesson_payload

    by_title = {}
    for row in AgentMemory.query.filter(AgentMemory.type == "lesson").all():
        title = str(_lesson_payload(row).get("title") or "").strip().lower()
        if title and title not in by_title:
            by_title[title] = row

    data = json.loads(path.read_text())
    for entry in data.get("lessons") or []:
        ok, err = validate_lesson_payload(entry)
        if not ok:
            logger.warning("Shipped lesson in %s skipped: %s", path.name, err)
            continue
        title = str(entry.get("title") or "").strip()
        key = title.lower()
        payload = {"title": title, "steps": entry["steps"], "parameters": entry.get("parameters") or []}
        shipped = _lesson_fingerprint(payload["steps"], payload["parameters"])
        last = applied.get(key)
        tags = normalize_tags(entry.get("tags"))
        importance = coerce_importance(entry.get("importance", 0.8), "lesson")
        row = by_title.get(key)

        if row is None:
            if last is not None:
                counts["kept"] += 1
                continue
            memory = add_memory(content=json.dumps(payload), memory_type="lesson", source="bundle",
                                importance=importance, tags=tags)
            if memory is None:
                logger.warning("Shipped lesson %r could not be added", title)
                continue
            if not getattr(memory, "already_saved", False):
                counts["added"] += 1
            applied[key] = shipped
            continue

        stored = _lesson_payload(row)
        current = _lesson_fingerprint(stored.get("steps"), stored.get("parameters"))
        if current == shipped:
            applied[key] = shipped
            continue
        if last is None or current != last or getattr(row, "status", "active") != "active":
            counts["kept"] += 1
            continue
        row.content = json.dumps(payload)
        row.extra_data = {**(row.extra_data or {}), "lesson": payload}
        row.tags = json.dumps(tags) if tags else None
        row.importance = importance
        row.updated_at = datetime.now()
        applied[key] = shipped
        counts["updated"] += 1


def apply_shipped_bundles(rule_dir: Optional[Path] = None, lesson_dir: Optional[Path] = None) -> Dict[str, int]:
    """Apply every shipped bundle under the rules above. Call inside an app context."""
    from backend.models import Setting, db

    counts = {"added": 0, "updated": 0, "kept": 0}
    if os.environ.get("GUAARDVARK_SKIP_BUNDLES", "").strip().lower() in ("1", "true", "yes"):
        return counts
    record = _load_record(db, Setting)
    for folder, kind, apply in ((rule_dir or RULE_BUNDLE_DIR, "rules", _apply_rules),
                                (lesson_dir or LESSON_BUNDLE_DIR, "lessons", _apply_lessons)):
        for path in sorted(Path(folder).glob("*.json")):
            applied = record.setdefault(f"{kind}:{path.name}", {})
            try:
                apply(path, applied, counts)
                db.session.flush()
            except Exception as e:  # noqa: BLE001 - one bad bundle must not stop startup
                logger.warning("Shipped bundle %s not applied: %s", path.name, e)
                db.session.rollback()
                return counts
    _save_record(db, Setting, record)
    db.session.commit()
    if counts["added"] or counts["updated"]:
        logger.info("Shipped bundles applied: %s", counts)
    return counts
