# backend/seed_data.py
# Database seeding utilities - NO DUMMY DATA
# Only seeds essential system data, not fake clients/projects

import logging
import os
import sys
from datetime import datetime
from typing import Optional, Dict

# Add backend to Python path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

try:
    from backend.app import app, db
    # Model (previously ModelInfo) is kept as an alias so legacy seed code still runs.
    from backend.models import Rule, Client, Project, Website, Task, Model as ModelInfo
    import json

    logger = logging.getLogger(__name__)
except ImportError as e:
    print(f"Error importing required modules: {e}")
    print("Make sure you're running this from the backend directory.")
    sys.exit(1)


def seed_rules_from_file(rules_file: Optional[str] = None):
    """Load system rules from seed_rules.json into the database.

    Only inserts rules that don't already exist (by name).
    Safe to call multiple times.
    """
    if rules_file is None:
        rules_file = os.path.join(os.path.dirname(__file__), "seed_rules.json")

    if not os.path.exists(rules_file):
        logger.warning(f"Seed rules file not found: {rules_file}")
        return 0

    try:
        with open(rules_file, "r") as f:
            data = json.load(f)

        rules_data = data.get("rules", [])
        if not rules_data:
            logger.info("No rules in seed file.")
            return 0

        inserted = 0
        for rule_data in rules_data:
            # Skip if rule with this name already exists
            existing = Rule.query.filter_by(name=rule_data["name"]).first()
            if existing:
                continue

            rule = Rule(
                name=rule_data["name"],
                level=rule_data.get("level", "USER_GLOBAL"),
                type=rule_data.get("type", "PROMPT_TEMPLATE"),
                command_label=rule_data.get("command_label"),
                rule_text=rule_data["rule_text"],
                description=rule_data.get("description", ""),
                output_schema_name=rule_data.get("output_schema_name"),
                target_models_json=rule_data.get("target_models_json", '["__ALL__"]'),
                is_active=rule_data.get("is_active", False),
            )
            db.session.add(rule)
            inserted += 1

        db.session.commit()
        logger.info(f"Seeded {inserted} rules from {rules_file}")
        return inserted

    except Exception as e:
        logger.error(f"Error seeding rules: {e}")
        db.session.rollback()
        return 0


def load_rule_bundle(bundle_file: str) -> Dict[str, int]:
    """Apply a rule bundle to the database, upserting by rule name.

    A bundle is the voice of one distribution — the engine's own or a vertical's
    — and a distribution has to be able to re-apply it after editing the text,
    flipping ``is_active`` or changing model targets. ``seed_rules_from_file``
    cannot do that: it skips every name it has already seen, so a rule that
    shipped inactive stays inactive forever.

    For each entry the row with the same name, level and type is updated in
    place; any other active row carrying that name is deactivated first, so the
    partial unique index on active identity is never violated; a missing row is
    inserted. ``is_active`` comes from the bundle. Returns counts.
    """
    with open(bundle_file, "r") as f:
        data = json.load(f)

    counts = {"inserted": 0, "updated": 0, "deactivated": 0}
    for entry in data.get("rules", []):
        name = entry["name"]
        level = entry.get("level", "SYSTEM")
        rule_type = entry.get("type", "PROMPT_TEMPLATE")
        active = bool(entry.get("is_active", True))

        rows = Rule.query.filter_by(name=name).all()
        match = next((r for r in rows if r.level == level and r.type == rule_type), None)

        if active:
            for row in rows:
                if row is not match and row.is_active:
                    row.is_active = False
                    counts["deactivated"] += 1
            # The unique index is checked per statement: retire the old identity
            # before the new one becomes active.
            db.session.flush()

        if match is None:
            match = Rule(name=name, level=level, type=rule_type, rule_text=entry["rule_text"])
            db.session.add(match)
            counts["inserted"] += 1
        else:
            counts["updated"] += 1

        match.rule_text = entry["rule_text"]
        match.description = entry.get("description", "")
        match.command_label = entry.get("command_label")
        match.output_schema_name = entry.get("output_schema_name")
        match.target_models_json = entry.get("target_models_json", '["__ALL__"]')
        match.is_active = active
        # The active-prompt cache is keyed on the newest updated_at; bump it even
        # when nothing else changed so a re-applied bundle is picked up.
        match.updated_at = datetime.now()

    db.session.commit()
    logger.info("Applied rule bundle %s: %s", bundle_file, counts)
    return counts


def _lesson_title_from_row(row) -> str:
    """Title stored in lesson JSON content, falling back to extra_data."""
    payload = None
    try:
        decoded = json.loads(row.content or "")
        if isinstance(decoded, dict):
            payload = decoded
    except (json.JSONDecodeError, TypeError):
        payload = None
    if payload is None:
        extra = row.extra_data if isinstance(getattr(row, "extra_data", None), dict) else {}
        lesson = extra.get("lesson") if isinstance(extra.get("lesson"), dict) else {}
        payload = lesson
    return str((payload or {}).get("title") or "").strip()


def load_lesson_bundle(path: str) -> Dict[str, int]:
    """Apply a lesson bundle, upserting procedures by title.

    Each entry is validated with ``validate_lesson_payload``. A lesson row with
    the same title is updated in place; otherwise a new row is written via
    ``add_memory`` with ``memory_type="lesson"`` and ``source="bundle"``.
    Re-applying a bundle does not create duplicates.
    """
    from backend.api.memory_api import add_memory
    from backend.models import AgentMemory
    from backend.services.memory_contract import (
        coerce_importance,
        normalize_tags,
        validate_lesson_payload,
    )

    with open(path, "r") as f:
        data = json.load(f)

    counts = {"inserted": 0, "updated": 0, "invalid": 0}
    existing_by_title = {}
    for row in AgentMemory.query.filter(AgentMemory.type == "lesson").all():
        title = _lesson_title_from_row(row)
        if title and title.lower() not in existing_by_title:
            existing_by_title[title.lower()] = row

    for entry in data.get("lessons") or []:
        ok, err = validate_lesson_payload(entry)
        if not ok:
            logger.warning("Skipping invalid lesson in %s: %s", path, err)
            counts["invalid"] += 1
            continue

        title = str(entry.get("title") or "").strip()
        payload = {
            "title": title,
            "steps": entry["steps"],
            "parameters": entry.get("parameters") or [],
        }
        content = json.dumps(payload)
        tags = normalize_tags(entry.get("tags"))
        importance = coerce_importance(entry.get("importance", 0.8), "lesson")
        match = existing_by_title.get(title.lower())

        if match is None:
            memory = add_memory(
                content=content,
                memory_type="lesson",
                source="bundle",
                importance=importance,
                tags=tags,
            )
            if memory is None:
                logger.warning("Failed to insert lesson %r from %s", title, path)
                counts["invalid"] += 1
                continue
            existing_by_title[title.lower()] = memory
            counts["inserted"] += 1
        else:
            match.content = content
            extra = dict(match.extra_data or {})
            extra["lesson"] = payload
            match.extra_data = extra
            match.tags = json.dumps(tags) if tags else None
            match.importance = importance
            match.source = "bundle"
            match.status = "active"
            match.updated_at = datetime.now()
            db.session.commit()
            existing_by_title[title.lower()] = match
            counts["updated"] += 1

    logger.info("Applied lesson bundle %s: %s", path, counts)
    return counts


def seed_essential_system_data():
    """Seeds only essential system data - NO dummy client data."""
    logger.info("Seeding essential system data...")

    try:
        # Seed system rules from export file
        count = seed_rules_from_file()
        if count > 0:
            logger.info(f"Seeded {count} system rules.")

        db.session.commit()
        logger.info("Essential system data seeded successfully.")

    except Exception as e:
        logger.error(f"Error seeding essential data: {e}")
        db.session.rollback()
        raise


def seed_demo_data():
    """
    Seeds demo/development data ONLY if explicitly requested.
    Use environment variable SEED_DEMO_DATA=true to enable.
    """
    if not os.getenv('SEED_DEMO_DATA', '').lower() == 'true':
        logger.info(" Demo data seeding skipped. Set SEED_DEMO_DATA=true to enable.")
        return
        
    logger.info("Seeding demo data (development only)...")
    
    try:
        # Demo Client (only for development)
        demo_client = Client.query.filter_by(name="Demo Client").first()
        if not demo_client:
            demo_client = Client(
                name="Demo Client", 
                notes="Development demo client - remove in production"
            )
            db.session.add(demo_client)
            logger.info("Added Demo Client (development only).")
        
        db.session.commit()
        
        # Demo Project
        demo_project = Project.query.filter_by(name="Demo Project").first()
        if not demo_project and demo_client:
            demo_project = Project(
                name="Demo Project", 
                description="Development demo project - remove in production"
            )
            demo_project.client_id = demo_client.id
            db.session.add(demo_project)
            logger.info("Added Demo Project (development only).")
        
        db.session.commit()
        logger.info("Demo data seeded. Remember to remove in production.")
        
    except Exception as e:
        logger.error(f"Error seeding demo data: {e}")
        db.session.rollback()
        raise


def seed_database():
    """Main seeding function - seeds essential data and optionally demo data."""
    logger.info("Starting database seeding...")
    
    # Always seed essential system data
    seed_essential_system_data()
    
    # Only seed demo data if explicitly requested
    seed_demo_data()
    
    logger.info("Database seeding completed.")


# --- CLI Integration ---
if __name__ == "__main__":
    with app.app_context():
        seed_database()
