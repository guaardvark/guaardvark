# backend/seed_data.py
# Database seeding utilities - NO DUMMY DATA
# Only seeds essential system data, not fake clients/projects

import logging
import os
import sys
from typing import Optional

# Add backend to Python path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

try:
    from backend.app import app, db
    from backend.models import Rule, Client, Project, Website, Task, ModelInfo
    from backend.utils.prompt_utils import (
        FALLBACK_QA_PROMPT_TEXT,
        FALLBACK_CODE_GEN_PROMPT_TEXT,
    )
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
    if not os.getenv("SEED_DEMO_DATA", "").lower() == "true":
        logger.info(" Demo data seeding skipped. Set SEED_DEMO_DATA=true to enable.")
        return

    logger.info("Seeding demo data (development only)...")

    try:
        # Demo Client (only for development)
        demo_client = Client.query.filter_by(name="Demo Client").first()
        if not demo_client:
            demo_client = Client(
                name="Demo Client",
                notes="Development demo client - remove in production",
            )
            db.session.add(demo_client)
            logger.info("Added Demo Client (development only).")

        db.session.commit()

        # Demo Project
        demo_project = Project.query.filter_by(name="Demo Project").first()
        if not demo_project and demo_client:
            demo_project = Project(
                name="Demo Project",
                description="Development demo project - remove in production",
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
    logger.info("🌱 Starting database seeding...")

    # Always seed essential system data
    seed_essential_system_data()

    # Only seed demo data if explicitly requested
    seed_demo_data()

    logger.info("Database seeding completed.")


# --- CLI Integration ---
if __name__ == "__main__":
    with app.app_context():
        seed_database()
