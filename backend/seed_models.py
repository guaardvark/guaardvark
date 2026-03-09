# seed_models.py - Dynamic Model Seeding
# NO HARDCODED MODEL LISTS - Uses dynamic detection from Ollama API

import logging
import os
import sys

# Add the parent directory to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Configure logging for the script itself
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s"
)
logger = logging.getLogger(__name__)

try:
    from backend.app import app
    from backend.models import ModelInfo, db
    from backend.utils.model_utils import get_available_ollama_models

    logger.info("Successfully imported backend modules.")
except ImportError as e:
    logger.error(f"Error importing backend modules: {e}", exc_info=True)
    logger.error("Please ensure you are running this script from the 'backend' directory")
    logger.error(f"Project root determined as: {project_root}")
    sys.exit(1)


def seed_models_from_ollama():
    """
    Dynamically detect and seed models from current Ollama installation.
    NO hardcoded model lists - uses real data only.
    """
    logger.info("🌱 Seeding models from Ollama API...")
    
    # Get available models from Ollama API (dynamic detection)
    models_data = get_available_ollama_models()
    
    if isinstance(models_data, dict) and models_data.get("error"):
        logger.error(f"Failed to get models from Ollama: {models_data['error']}")
        logger.warning(" Make sure Ollama is running and accessible")
        return
    
    if not models_data:
        logger.warning(" No models found in Ollama installation")
        return
    
    logger.info(f"📡 Found {len(models_data)} models in Ollama installation")
    
    added_count = 0
    skipped_count = 0
    
    try:
        for model_data in models_data:
            model_name = model_data.get('name', 'unknown')
            
            # Check if model already exists in our database
            exists = db.session.query(ModelInfo).filter_by(name=model_name).first()
            
            if not exists:
                logger.info(f"  ➕ Adding model: {model_name}")
                
                # Create model entry with dynamic data from Ollama
                model_info = ModelInfo(
                    name=model_name,
                    size=model_data.get('size', 0),
                    modified_at=model_data.get('modified_at'),
                    digest=model_data.get('digest', ''),
                    details=model_data.get('details', {}),
                    is_vision_model=model_data.get('is_vision_model', False)
                )
                
                db.session.add(model_info)
                added_count += 1
            else:
                logger.info(f"  Skipping existing model: {model_name}")
                skipped_count += 1

        # Commit only if new models were added
        if added_count > 0:
            db.session.commit()
            logger.info(f"Successfully committed {added_count} new model(s). Skipped {skipped_count}.")
        else:
            logger.info(f"No new models to add. Skipped {skipped_count} existing model(s).")

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error during database operation: {e}", exc_info=True)
        logger.error("Database changes rolled back.")


# Use the application context from the imported app
if __name__ == "__main__":
    with app.app_context():
        seed_models_from_ollama()
        logger.info("🏁 Seed script finished.")
