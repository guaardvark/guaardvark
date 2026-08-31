import logging
import os
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

try:
    from loguru import logger as loguru_logger
except Exception:  # pragma: no cover - optional dependency
    loguru_logger = logging.getLogger(__name__)
from backend.models import Setting, SystemSetting, db
from backend.utils.response_utils import error_response, success_response
from backend.utils.password_validation import validate_password_strength
from backend.utils.settings_utils import get_web_access

settings_bp = Blueprint("settings_api", __name__, url_prefix="/api/settings")


_ADV_HANDLER_ID = None


def _set_logging_level(enabled: bool) -> None:
    """Adjust root logger levels and manage loguru debug file."""
    global _ADV_HANDLER_ID
    level = logging.DEBUG if enabled else logging.INFO
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    for handler in root_logger.handlers:
        handler.setLevel(level)

    if enabled and _ADV_HANDLER_ID is None:
        log_file = os.path.join(
            current_app.config.get("LOG_DIR", "logs"),
            f"debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
        )
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        _ADV_HANDLER_ID = loguru_logger.add(
            log_file, level="DEBUG", format="{time} {level} {message}", serialize=False
        )
        loguru_logger.debug("Advanced debug logging enabled")
    elif not enabled and _ADV_HANDLER_ID is not None:
        loguru_logger.remove(_ADV_HANDLER_ID)
        loguru_logger.debug("Advanced debug logging disabled")
        _ADV_HANDLER_ID = None


@settings_bp.route("/web_access", methods=["GET"])
def get_web_access_route():
    allow = get_web_access()
    return success_response({"allow_web_search": allow})


@settings_bp.route("/web_access", methods=["POST"])
def set_web_access():
    if not request.is_json:
        return error_response("Request must be JSON")
    data = request.get_json()
    allow = bool(data.get("allow_web_search"))
    try:
        setting = db.session.get(Setting, "allow_web_search")
        if setting:
            setting.value = "true" if allow else "false"
        else:
            setting = Setting(
                key="allow_web_search", value="true" if allow else "false"
            )
            db.session.add(setting)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to update web access setting: {e}")
        return error_response("Failed to update setting", status_code=500)
    return success_response({"allow_web_search": allow})


@settings_bp.route("/address_provider", methods=["GET"])
def get_address_provider():
    """Address-suggestion provider status. The key itself is never returned."""
    from backend.services import address_lookup

    return success_response(
        {
            "provider": address_lookup.provider_name(),
            "has_key": bool(address_lookup.api_key()),
            "available": address_lookup.is_configured(),
            "unavailable_reason": address_lookup.unavailable_reason(),
            "attribution": address_lookup.ATTRIBUTION,
        }
    )


@settings_bp.route("/address_provider", methods=["POST"])
def set_address_provider():
    """Store the provider name and/or key. An empty key clears it.

    Address fields keep working from on-file addresses either way; this only
    controls the optional third-party source.
    """
    from backend.services import address_lookup

    if not request.is_json:
        return error_response("Request must be JSON")
    data = request.get_json() or {}
    updates = {}
    if "provider" in data:
        updates[address_lookup.PROVIDER_SETTING] = (
            str(data.get("provider") or "").strip().lower()
            or address_lookup.DEFAULT_PROVIDER
        )
    if "api_key" in data:
        updates[address_lookup.API_KEY_SETTING] = str(data.get("api_key") or "").strip()
    if not updates:
        return error_response("Nothing to update")
    try:
        for key, value in updates.items():
            setting = db.session.get(Setting, key)
            if setting:
                setting.value = value
            else:
                db.session.add(Setting(key=key, value=value))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to update address provider setting: {e}")
        return error_response("Failed to update setting", status_code=500)
    return success_response(
        {
            "provider": address_lookup.provider_name(),
            "has_key": bool(address_lookup.api_key()),
            "available": address_lookup.is_configured(),
            "unavailable_reason": address_lookup.unavailable_reason(),
        }
    )


@settings_bp.route("/verbatim_prompts", methods=["GET"])
def get_verbatim_prompts():
    """Whether image/video prompts go to the model verbatim (director-LLM rewrite OFF)."""
    try:
        row = db.session.get(SystemSetting, "verbatim_prompts")
        enabled = bool(row and str(row.value).lower() == "true")
    except Exception:
        enabled = False
    return success_response({"enabled": enabled})


@settings_bp.route("/chat_image_model", methods=["GET"])
def get_chat_image_model_route():
    """Persisted Stable Diffusion / image-edit model for chat (/imagemodel, generate_image, edit_image)."""
    from backend.utils.settings_utils import get_chat_image_model

    return success_response({"model": get_chat_image_model()})


@settings_bp.route("/chat_image_model", methods=["POST"])
def set_chat_image_model_route():
    """Set the chat image model (e.g. auto, zimage-turbo, sd-xl, kontext)."""
    if not request.is_json:
        return error_response("Request must be JSON")
    model = (request.get_json().get("model") or "auto").strip() or "auto"
    try:
        from backend.utils.settings_utils import save_setting

        save_setting("chat_image_model", model)
    except Exception as e:
        current_app.logger.error(f"Failed to update chat_image_model setting: {e}")
        return error_response("Failed to update setting", status_code=500)
    return success_response({"model": model})


@settings_bp.route("/media_models", methods=["GET"])
def get_media_models():
    """Stills / cast-train / max-quality model registry + current selections.

    Same philosophy as the Ollama model picker: one registry, Settings chooses
    defaults, Cast/Image Gen honor them. Z-Image Turbo is the product default;
    FLUX is max-quality; SDXL is legacy only.
    """
    try:
        from backend.services.media_model_registry import public_settings_payload
        return success_response(public_settings_payload())
    except Exception as e:
        current_app.logger.error(f"Failed to read media models: {e}")
        return error_response("Failed to read media models", status_code=500)


@settings_bp.route("/media_models", methods=["POST"])
def set_media_models():
    """Update media model defaults. Body may include any of:
    stills_model, cast_train_base, max_quality_model,
    character_lora_strength_zimage / _sdxl / _flux.
    """
    if not request.is_json:
        return error_response("Request must be JSON")
    data = request.get_json() or {}
    try:
        from backend.services import media_model_registry as mmr
        out = {}
        if "stills_model" in data and data["stills_model"] is not None:
            out["stills_model"] = mmr.set_stills_model_setting(str(data["stills_model"]))
        if "cast_train_base" in data and data["cast_train_base"] is not None:
            out["cast_train_base"] = mmr.set_cast_train_base_setting(str(data["cast_train_base"]))
        if "max_quality_model" in data and data["max_quality_model"] is not None:
            out["max_quality_model"] = mmr.set_max_quality_model_setting(str(data["max_quality_model"]))
        for fam_key, fam in (
            ("character_lora_strength_zimage", "zimage"),
            ("character_lora_strength_sdxl", "sdxl"),
            ("character_lora_strength_flux", "flux"),
        ):
            if fam_key in data and data[fam_key] is not None:
                out[fam_key] = mmr.set_character_lora_strength(fam, float(data[fam_key]))
        payload = mmr.public_settings_payload()
        payload["updated"] = out
        return success_response(payload)
    except ValueError as e:
        return error_response(str(e), status_code=400)
    except Exception as e:
        current_app.logger.error(f"Failed to update media models: {e}")
        return error_response("Failed to update media models", status_code=500)


@settings_bp.route("/verbatim_prompts", methods=["POST"])
def set_verbatim_prompts():
    """Toggle 'verbatim prompts' — when ON, the user's EXACT words go straight to the
    image/video model with no gemma enrichment/softening. Off by default. (env
    VERBATIM_PROMPTS=1 force-enables regardless, for a guaranteed-on live demo.)"""
    if not request.is_json:
        return error_response("Request must be JSON")
    enabled = bool(request.get_json().get("enabled"))
    try:
        row = db.session.get(SystemSetting, "verbatim_prompts")
        if row:
            row.value = "true" if enabled else "false"
        else:
            db.session.add(SystemSetting(key="verbatim_prompts", value="true" if enabled else "false"))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to update verbatim_prompts setting: {e}")
        return error_response("Failed to update setting", status_code=500)
    return success_response({"enabled": enabled})


@settings_bp.route("/advanced_debug", methods=["GET"])
def get_advanced_debug():
    enabled = False
    try:
        setting = db.session.get(Setting, "advanced_debug")
        if setting and setting.value == "true":
            enabled = True
    except Exception as e:
        current_app.logger.error(f"Failed to read advanced debug setting: {e}")
    return success_response({"advanced_debug": enabled})


@settings_bp.route("/advanced_debug", methods=["POST"])
def set_advanced_debug():
    if not request.is_json:
        return error_response("Request must be JSON")
    data = request.get_json()
    enabled = bool(data.get("advanced_debug"))
    try:
        setting = db.session.get(Setting, "advanced_debug")
        if setting:
            setting.value = "true" if enabled else "false"
        else:
            setting = Setting(
                key="advanced_debug", value="true" if enabled else "false"
            )
            db.session.add(setting)
        db.session.commit()
        _set_logging_level(enabled)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"Failed to update advanced debug setting: {e}", exc_info=True
        )
        return error_response("Failed to update setting", status_code=500)
    return success_response({"advanced_debug": enabled})


@settings_bp.route("/llm_debug", methods=["GET"])
def get_llm_debug():
    enabled = False
    try:
        setting = db.session.get(Setting, "llm_debug")
        if setting and setting.value == "true":
            enabled = True
    except Exception as e:
        current_app.logger.error(f"Failed to read llm_debug setting: {e}")
    return success_response({"llm_debug": enabled})


@settings_bp.route("/llm_debug", methods=["POST"])
def set_llm_debug():
    if not request.is_json:
        return error_response("Request must be JSON")
    data = request.get_json()
    enabled = bool(data.get("llm_debug"))
    try:
        setting = db.session.get(Setting, "llm_debug")
        if setting:
            setting.value = "true" if enabled else "false"
        else:
            setting = Setting(
                key="llm_debug", value="true" if enabled else "false"
            )
            db.session.add(setting)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"Failed to update llm_debug setting: {e}", exc_info=True
        )
        return error_response("Failed to update setting", status_code=500)
    return success_response({"llm_debug": enabled})


@settings_bp.route("/rules_enabled", methods=["GET"])
def get_rules_enabled():
    """Global chat-rules toggle. When off, chat engines skip RulesPage
    lookups and use the hardcoded default prompt. Default: off."""
    enabled = False
    try:
        setting = db.session.get(Setting, "rules_enabled")
        if setting and (setting.value or "").lower() in ("true", "1", "yes"):
            enabled = True
    except Exception as e:
        current_app.logger.error(f"Failed to read rules_enabled setting: {e}")
    return success_response({"rules_enabled": enabled})


@settings_bp.route("/rules_enabled", methods=["POST"])
def set_rules_enabled():
    if not request.is_json:
        return error_response("Request must be JSON")
    data = request.get_json()
    enabled = bool(data.get("rules_enabled"))
    try:
        setting = db.session.get(Setting, "rules_enabled")
        if setting:
            setting.value = "true" if enabled else "false"
        else:
            setting = Setting(
                key="rules_enabled", value="true" if enabled else "false"
            )
            db.session.add(setting)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"Failed to update rules_enabled setting: {e}", exc_info=True
        )
        return error_response("Failed to update setting", status_code=500)
    return success_response({"rules_enabled": enabled})


@settings_bp.route("/chat_thinking_default", methods=["GET"])
def get_chat_thinking_default():
    """Global default for whether thinking-capable models (gemma4:12b, qwen3, ...)
    use chain-of-thought in chat. Off = faster replies. Per-chat /thinking on|off
    overrides this. Default: off."""
    enabled = False
    try:
        setting = db.session.get(Setting, "chat_thinking_default")
        if setting and (setting.value or "").lower() in ("true", "1", "yes"):
            enabled = True
    except Exception as e:
        current_app.logger.error(f"Failed to read chat_thinking_default setting: {e}")
    return success_response({"chat_thinking_default": enabled})


@settings_bp.route("/chat_thinking_default", methods=["POST"])
def set_chat_thinking_default():
    if not request.is_json:
        return error_response("Request must be JSON")
    data = request.get_json()
    enabled = bool(data.get("chat_thinking_default"))
    try:
        setting = db.session.get(Setting, "chat_thinking_default")
        if setting:
            setting.value = "true" if enabled else "false"
        else:
            setting = Setting(
                key="chat_thinking_default", value="true" if enabled else "false"
            )
            db.session.add(setting)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"Failed to update chat_thinking_default setting: {e}", exc_info=True
        )
        return error_response("Failed to update setting", status_code=500)
    return success_response({"chat_thinking_default": enabled})


@settings_bp.route("/behavior_learning", methods=["GET"])
def get_behavior_learning():
    enabled = False
    try:
        setting = db.session.get(Setting, "behavior_learning_enabled")
        if setting and setting.value == "true":
            enabled = True
    except Exception as e:
        current_app.logger.error(f"Failed to read behavior learning setting: {e}")
    return success_response({"behavior_learning_enabled": enabled})


@settings_bp.route("/behavior_learning", methods=["POST"])
def set_behavior_learning():
    if not request.is_json:
        return error_response("Request must be JSON")
    data = request.get_json()
    enabled = bool(data.get("behavior_learning_enabled"))
    try:
        setting = db.session.get(Setting, "behavior_learning_enabled")
        if setting:
            setting.value = "true" if enabled else "false"
        else:
            setting = Setting(
                key="behavior_learning_enabled", value="true" if enabled else "false"
            )
            db.session.add(setting)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"Failed to update behavior learning setting: {e}", exc_info=True
        )
        return error_response("Failed to update setting", status_code=500)
    return success_response({"behavior_learning_enabled": enabled})


@settings_bp.route("/branding", methods=["GET"])
def get_branding():
    """Return system branding settings."""
    name = None
    logo = None
    try:
        name_row = db.session.get(SystemSetting, "system_name")
        logo_row = db.session.get(SystemSetting, "logo_path")
        if name_row:
            name = name_row.value
        if logo_row:
            logo = logo_row.value
    except Exception as e:
        current_app.logger.error(f"Failed to read system settings: {e}")
    # Default to profile-default.png if no logo set
    if not logo:
        logo = "system/profile-default.png"
    # The active profile rides on this response: it is the only config fetch
    # the frontend makes at startup. DB branding wins over the profile's brand.
    from backend.profiles import active_profile
    profile = active_profile()
    if not name and profile.brand.get("app_name"):
        name = profile.brand["app_name"]
    return success_response({"system_name": name, "logo_path": logo, "profile": profile.public_dict()})


@settings_bp.route("/profile", methods=["GET"])
def get_profile():
    """The running profile, the one .env names for the next start, and the choices."""
    from backend import profiles as P
    active = P.active_profile()
    available = []
    for name, (source, path) in P.available_profiles().items():
        try:
            loaded = P.load_profile(name)
            available.append({"name": name, "label": loaded.label, "description": loaded.description, "source": source})
        except Exception:  # pragma: no cover - load_profile never raises
            available.append({"name": name, "label": name, "description": "", "source": source})
    from backend import extensions as X
    return success_response({
        "active": active.public_dict(),
        "configured": P.configured_name() or P.DEFAULT_PROFILE,
        "available": available,
        "env_writable": P.env_file_writable(),
        "extensions": X.load_report(),
    })


@settings_bp.route("/profile", methods=["POST"])
def set_profile():
    """Persist a profile choice to .env. Applies on the next start: feature
    flags are read once at boot, so a live switch would half-apply."""
    from backend import profiles as P
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    try:
        P.set_configured_name(name)
    except ValueError as e:
        return error_response(str(e), 400)
    except OSError as e:
        return error_response(f"could not write .env: {e}", 500)
    return success_response({"name": name, "restart_required": True})


@settings_bp.route("/branding", methods=["POST"])
def set_branding():
    """Update system name and/or logo."""
    name = request.form.get("system_name")
    file = request.files.get("logo")
    logo_rel = None
    try:
        if name is not None:
            row = db.session.get(SystemSetting, "system_name")
            if row:
                row.value = name
            else:
                db.session.add(SystemSetting(key="system_name", value=name))
        if file:
            # Validate file is an image
            if not file.filename or '.' not in file.filename:
                return error_response("Invalid file: must be an image file", status_code=400)
            
            # Get file extension
            file_ext = file.filename.rsplit('.', 1)[1].lower()
            if file_ext not in ['png', 'jpg', 'jpeg', 'gif', 'webp']:
                return error_response("Invalid file type: must be PNG, JPG, JPEG, GIF, or WEBP", status_code=400)
            
            upload_dir = os.path.join(
                current_app.config.get("UPLOAD_FOLDER", "uploads"), "system"
            )
            os.makedirs(upload_dir, exist_ok=True)
            
            # Keep original filename and extension
            from werkzeug.utils import secure_filename
            safe_filename = secure_filename(file.filename)
            logo_rel = os.path.join("system", safe_filename)
            save_path = os.path.join(upload_dir, safe_filename)
            
            file.save(save_path)
            current_app.logger.info(f"Logo saved to: {save_path}")
            current_app.logger.info(f"Logo relative path: {logo_rel}")
            current_app.logger.info(f"Uploads folder: {current_app.config.get('UPLOAD_FOLDER', 'uploads')}")
            current_app.logger.info(f"File exists check: {os.path.exists(save_path)}")
            
            row = db.session.get(SystemSetting, "logo_path")
            if row:
                row.value = logo_rel
            else:
                db.session.add(SystemSetting(key="logo_path", value=logo_rel))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to update branding: {e}", exc_info=True)
        return error_response("Failed to update branding", status_code=500)
    return success_response({"system_name": name, "logo_path": logo_rel})


@settings_bp.route("/rag_debug", methods=["GET"])
def get_rag_debug():
    enabled = False
    try:
        setting = db.session.get(Setting, "rag_debug_enabled")
        if setting and setting.value == "true":
            enabled = True
    except Exception as e:
        current_app.logger.error(f"Failed to read RAG debug setting: {e}")
    return success_response({"rag_debug_enabled": enabled})


@settings_bp.route("/rag_debug", methods=["POST"])
def set_rag_debug():
    if not request.is_json:
        return error_response("Request must be JSON")
    data = request.get_json()
    enabled = bool(data.get("rag_debug_enabled"))
    try:
        setting = db.session.get(Setting, "rag_debug_enabled")
        if setting:
            setting.value = "true" if enabled else "false"
        else:
            setting = Setting(
                key="rag_debug_enabled", value="true" if enabled else "false"
            )
            db.session.add(setting)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"Failed to update RAG debug setting: {e}", exc_info=True
        )
        return error_response("Failed to update setting", status_code=500)
    return success_response({"rag_debug_enabled": enabled})


@settings_bp.route("/rag-features", methods=["GET"])
def get_rag_features():
    """Get all RAG-related feature settings"""
    try:
        from backend.config import ENHANCED_CONTEXT_ENABLED, ADVANCED_RAG_ENABLED, RAG_DEBUG_ENABLED
        
        # Get database settings (runtime overrides)
        enhanced_context = ENHANCED_CONTEXT_ENABLED
        advanced_rag = ADVANCED_RAG_ENABLED  
        rag_debug = RAG_DEBUG_ENABLED
        
        # Check for database overrides
        try:
            context_setting = db.session.get(Setting, "enhanced_context_enabled")
            if context_setting:
                enhanced_context = context_setting.value == "true"
                
            rag_setting = db.session.get(Setting, "advanced_rag_enabled")
            if rag_setting:
                advanced_rag = rag_setting.value == "true"
                
            debug_setting = db.session.get(Setting, "rag_debug_enabled")
            if debug_setting:
                rag_debug = debug_setting.value == "true"
                
        except Exception as db_error:
            current_app.logger.warning(f"Failed to read RAG settings from database: {db_error}")
        
        return success_response({
            "enhanced_context": enhanced_context,
            "advanced_rag": advanced_rag,
            "rag_debug": rag_debug
        })
        
    except Exception as e:
        current_app.logger.error(f"Failed to get RAG features: {e}", exc_info=True)
        return error_response("Failed to get RAG features", status_code=500)


@settings_bp.route("/rag-features", methods=["PUT", "POST"])
def update_rag_features():
    """Update RAG-related feature settings"""
    if not request.is_json:
        return error_response("Request must be JSON")
    
    data = request.get_json()
    
    try:
        # Update settings that are provided
        settings_to_update = {
            "enhanced_context_enabled": data.get("enhanced_context"),
            "advanced_rag_enabled": data.get("advanced_rag"), 
            "rag_debug_enabled": data.get("rag_debug")
        }
        
        updated_settings = {}
        
        for key, value in settings_to_update.items():
            if value is not None:  # Only update if explicitly provided
                bool_value = bool(value)
                str_value = "true" if bool_value else "false"
                
                setting = db.session.get(Setting, key)
                if setting:
                    setting.value = str_value
                else:
                    setting = Setting(key=key, value=str_value)
                    db.session.add(setting)
                
                # Map back to response format
                response_key = key.replace("_enabled", "").replace("_", "_")
                if key == "enhanced_context_enabled":
                    updated_settings["enhanced_context"] = bool_value
                elif key == "advanced_rag_enabled":
                    updated_settings["advanced_rag"] = bool_value
                elif key == "rag_debug_enabled":
                    updated_settings["rag_debug"] = bool_value
        
        db.session.commit()
        
        return success_response({
            "message": "RAG features updated successfully",
            "updated": updated_settings
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to update RAG features: {e}", exc_info=True)
        return error_response("Failed to update RAG features", status_code=500)


@settings_bp.route("/clear_behavior_log", methods=["POST"])
def clear_behavior_log():
    """Clear the user behavior log file."""
    try:
        import os

        from backend.utils.chat_utils import USER_BEHAVIOR_LOG_PATH
        
        if os.path.exists(USER_BEHAVIOR_LOG_PATH):
            # Clear the file by opening it in write mode
            with open(USER_BEHAVIOR_LOG_PATH, "w", encoding="utf-8") as f:
                f.write("")  # Write empty string to clear the file
            current_app.logger.info("User behavior log cleared successfully")
            return success_response(
                {"message": "User behavior log cleared successfully"}
            )
        else:
            return success_response(
                {"message": "User behavior log file does not exist"}
            )
    except Exception as e:
        current_app.logger.error(
            f"Failed to clear user behavior log: {e}", exc_info=True
        )
        return error_response(f"Failed to clear user behavior log: {e}")


@settings_bp.route("/password/validate", methods=["POST"])
def validate_password():
    """Validate password strength according to security requirements."""
    current_app.logger.info("API: Received POST /api/settings/password/validate request")
    
    try:
        data = request.get_json()
        if not data:
            return error_response("No data provided", status_code=400)
        
        password = data.get("password")
        username = data.get("username")
        strict = data.get("strict", True)
        
        if not password:
            return error_response("Password is required", status_code=400)
        
        validation_result = validate_password_strength(password, username, strict)
        
        return success_response({
            "valid": validation_result["is_valid"],
            "errors": validation_result["errors"],
            "requirements": validation_result["requirements"],
            "strength_level": validation_result["strength_level"]
        })
        
    except Exception as e:
        current_app.logger.error(f"Failed to validate password: {e}", exc_info=True)
        return error_response(f"Failed to validate password: {e}")


@settings_bp.route("/password/requirements", methods=["GET"])
def get_password_requirements():
    """Get password requirements for the application."""
    current_app.logger.info("API: Received GET /api/settings/password/requirements request")
    
    try:
        from backend.utils.password_validation import default_password_validator
        
        requirements = default_password_validator.generate_password_requirements_text()
        
        return success_response({
            "requirements": requirements,
            "min_length": default_password_validator.min_length,
            "require_uppercase": default_password_validator.require_uppercase,
            "require_lowercase": default_password_validator.require_lowercase,
            "require_digits": default_password_validator.require_digits,
            "require_special": default_password_validator.require_special,
            "min_special_count": default_password_validator.min_special_count,
            "max_repeating_chars": default_password_validator.max_repeating_chars,
            "forbid_common_passwords": default_password_validator.forbid_common_passwords
        })
        
    except Exception as e:
        current_app.logger.error(f"Failed to get password requirements: {e}", exc_info=True)
        return error_response(f"Failed to get password requirements: {e}")


@settings_bp.route("/security/check", methods=["GET"])
def security_check():
    """Run security checks on the application configuration."""
    current_app.logger.info("API: Received GET /api/settings/security/check request")
    
    try:
        from backend.tools.security_self_check import run_security_checks
        
        warnings = run_security_checks()
        
        # Additional runtime security checks
        additional_checks = []
        
        # Check if default secret key is being used
        if current_app.config.get("SECRET_KEY") == "dev-secret-key":
            additional_checks.append("Application is using default secret key - change in production")
        
        # Check if debug mode is enabled
        if current_app.debug:
            additional_checks.append("Debug mode is enabled - disable in production")
        
        # Check CORS configuration
        if os.getenv("FLASK_ENV") == "production":
            frontend_url = os.getenv("VITE_FRONTEND_URL", "http://localhost:5173")
            if "localhost" in frontend_url:
                additional_checks.append("Frontend URL contains localhost in production environment")
        
        all_warnings = warnings + additional_checks
        
        return success_response({
            "warnings": all_warnings,
            "warning_count": len(all_warnings),
            "security_level": "high" if len(all_warnings) == 0 else "medium" if len(all_warnings) < 3 else "low"
        })
        
    except Exception as e:
        current_app.logger.error(f"Failed to run security check: {e}", exc_info=True)
        return error_response(f"Failed to run security check: {e}")


@settings_bp.route("/music_directory", methods=["GET"])
def get_music_directory():
    value = ""
    try:
        setting = db.session.get(Setting, "music_directory")
        if setting and setting.value:
            value = setting.value
    except Exception as e:
        current_app.logger.error(f"Failed to read music_directory setting: {e}")
    return success_response({"music_directory": value})


@settings_bp.route("/music_directory", methods=["POST"])
def set_music_directory():
    if not request.is_json:
        return error_response("Request must be JSON")
    data = request.get_json()
    path = data.get("music_directory", "").strip()
    try:
        setting = db.session.get(Setting, "music_directory")
        if setting:
            setting.value = path
        else:
            setting = Setting(key="music_directory", value=path)
            db.session.add(setting)
        db.session.commit()
        return success_response({"music_directory": path})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to update music_directory setting: {e}")
        return error_response(f"Failed to update setting: {e}", status_code=500)


@settings_bp.route("/index_profiles", methods=["GET"])
def get_index_profiles():
    """List index profiles, their activation state, and each projection's real size."""
    try:
        from backend.services.index_profiles import load_profiles
        profiles = load_profiles()
    except Exception as e:
        current_app.logger.error(f"Failed to load index profiles: {e}")
        return error_response("Failed to load index profiles", status_code=500)

    out = []
    for p in profiles:
        entry = p.to_dict()
        # Report what the projection actually holds rather than whether it is
        # configured: a profile can be active and still have nothing indexed.
        try:
            from backend.services.indexing_service import vector_store_stats
            entry["projection"] = vector_store_stats(profile=p.name)
        except Exception as e:
            entry["projection"] = {"error": str(e)[:160]}
        out.append(entry)
    return success_response({"profiles": out})


@settings_bp.route("/index_profiles", methods=["POST"])
def set_index_profiles():
    """Activate a set of profiles, and optionally update one profile's settings.

    Body: {"active": ["default", "mcp"]} and/or {"profile": {...}}
    """
    if not request.is_json:
        return error_response("Request must be JSON")
    data = request.get_json() or {}

    try:
        from backend.services.index_profiles import (
            IndexProfile, load_profiles, save_profiles, set_active,
        )

        if isinstance(data.get("profile"), dict):
            incoming = IndexProfile.from_dict(data["profile"])
            if not incoming.name:
                return error_response("profile.name is required")
            profiles = load_profiles()
            for i, existing in enumerate(profiles):
                if existing.name == incoming.name:
                    # Activation is owned by the "active" field below, so an edit
                    # cannot silently switch a projection on.
                    incoming.active = existing.active
                    incoming.embed_dim = existing.embed_dim
                    profiles[i] = incoming
                    break
            else:
                profiles.append(incoming)
            save_profiles(profiles)

        if isinstance(data.get("active"), list):
            set_active([str(n) for n in data["active"]])

        from backend.services.index_profiles import load_profiles as reload_profiles
        return success_response({"profiles": [p.to_dict() for p in reload_profiles()]})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Failed to update index profiles: {e}", exc_info=True)
        return error_response(f"Failed to update index profiles: {e}", status_code=500)


@settings_bp.route("/index_profiles/<name>/rebuild", methods=["POST"])
def rebuild_index_profile(name):
    """Drop a profile's projection so it can be rebuilt from the Document registry.

    Deletion happens at the projection, never at the registry: the registry is the
    source of truth and dropping a derived table must not remove a document.
    """
    try:
        from backend.services.index_profiles import get_profile
        if get_profile(name) is None:
            return error_response(f"Unknown profile '{name}'", status_code=404)
        from backend.services.indexing_service import drop_vector_store
        result = drop_vector_store(profile=name)
        if result.get("error"):
            return error_response(f"Could not drop projection: {result['error']}", status_code=500)
        return success_response({
            "profile": name,
            "dropped": result.get("dropped", False),
            "table": result.get("table"),
            "note": "Projection cleared. Re-index documents to rebuild it.",
        })
    except Exception as e:
        current_app.logger.error(f"Failed to rebuild profile {name}: {e}", exc_info=True)
        return error_response(f"Failed to rebuild profile: {e}", status_code=500)
