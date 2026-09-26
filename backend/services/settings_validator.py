# backend/services/settings_validator.py
# Settings Validator Service - Validates image generation settings with model-specific rules
# Prevents invalid combinations and provides recommendations

import logging
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class ValidationResult:
    """Result of settings validation"""
    is_valid: bool
    warnings: List[str]
    errors: List[str]
    corrected_values: Dict[str, Any]
    recommendations: List[str]

# Model-specific settings: the numbers (guidance/steps envelopes, recommended
# values, canvas limits, engine) are declared in media_model_registry
# (IMAGE_FAMILY_SPECS / IMAGE_MODEL_LIMITS) and read through image_render_limits;
# what the UI says about each model stays here.
_MODEL_NOTES = {
    "sd-xl": {"best_for": ["high_res", "anatomy", "landscapes"],
              "warnings": ["Guidance > 9.0 causes black images"]},
    "sdxl-turbo": {"best_for": ["speed", "previews", "high_res"],
                   "warnings": ["Not for final quality images", "Guidance not used by turbo models"]},
    "sd-1.5": {"best_for": ["general", "speed", "reliability"], "warnings": []},
    "realistic-vision": {"best_for": ["faces", "portraits", "photorealism"], "warnings": []},
    "epic-realism": {"best_for": ["faces", "portraits", "cinematic"], "warnings": []},
    "zimage-turbo": {"best_for": ["versatile", "photorealism", "faces", "anatomy", "text", "high_res"],
                     "warnings": []},
    "flux-dev": {"best_for": ["max_quality", "prompt_adherence", "photorealism", "text", "high_res"],
                 "warnings": [
                     "Runs through ComfyUI (needs Comfy up + flux1-dev weights).",
                     "Heavy VRAM — batch max_workers forced to 1.",
                     "Flux Dev design range is ~2.0 MP total — not 2048×2048.",
                 ]},
    "krea2-turbo": {"best_for": ["aesthetic", "photorealism", "creative", "high_res", "versatile"],
                    "warnings": ["2K native is supported; high VRAM on 16GB cards."]},
    "krea2-raw": {"best_for": ["creative", "photorealism", "versatile", "high_res", "mature", "fine_tune_base"],
                  "warnings": [
                      "Slower than Turbo (~52 steps). Less safety post-training than Turbo.",
                      "2K native is supported; high VRAM on 16GB cards.",
                  ]},
}


def _model_settings() -> Dict[str, Dict[str, Any]]:
    from backend.services.image_render_limits import validator_settings
    return {mid: {**numbers, **_MODEL_NOTES.get(mid, {"best_for": [], "warnings": []})}
            for mid, numbers in validator_settings().items()}


MODEL_SETTINGS = _model_settings()


class SettingsValidator:
    """Validates image generation settings with model-specific rules."""

    def __init__(self):
        self.model_settings = MODEL_SETTINGS

    def validate_settings(self, 
                          model: str,
                          guidance: float,
                          steps: int,
                          width: int,
                          height: int,
                          auto_correct: bool = True) -> ValidationResult:
        """
        Validate generation settings for a specific model.
        
        Args:
            model: Model identifier
            guidance: Guidance scale value
            steps: Number of inference steps
            width: Image width
            height: Image height
            auto_correct: Whether to auto-correct invalid values
            
        Returns:
            ValidationResult with validation status, warnings, errors, and corrections
        """
        warnings = []
        errors = []
        corrected_values = {}
        recommendations = []

        # Get model configuration
        model_config = self.model_settings.get(model)
        if not model_config:
            # Unknown model, use safe defaults
            model_config = self.model_settings["sd-1.5"]
            warnings.append(f"Unknown model '{model}', using SD 1.5 validation rules")

        # Validate guidance scale
        # hard_clamp=False (default for modern models): warn + recommend only —
        # do not silently throttle quality when the user pushes the slider.
        # hard_clamp=True (legacy SDXL black-image hazards): auto-correct into range.
        hard = bool(model_config.get("hard_clamp", True))
        guidance_min, guidance_max = model_config["guidance_range"]
        if guidance < guidance_min or guidance > guidance_max:
            error_msg = (
                f"Guidance scale {guidance} is outside recommended range "
                f"({guidance_min}-{guidance_max}) for {model}"
            )
            if auto_correct and hard:
                corrected_guidance = max(guidance_min, min(guidance, guidance_max))
                corrected_values["guidance"] = corrected_guidance
                warnings.append(f"{error_msg}. Auto-corrected to {corrected_guidance}")
            elif auto_correct and not hard:
                # Absolute safety floor/ceiling only (avoid NaNs / absurd values)
                abs_lo, abs_hi = 0.0, 30.0
                if guidance < abs_lo or guidance > abs_hi:
                    corrected_values["guidance"] = max(abs_lo, min(guidance, abs_hi))
                    warnings.append(
                        f"Guidance {guidance} clamped to absolute safety bounds "
                        f"[{abs_lo}, {abs_hi}]"
                    )
                else:
                    warnings.append(
                        f"{error_msg}. Left as-is (quality slider owns this); "
                        f"recommended={model_config['recommended_guidance']}"
                    )
            else:
                errors.append(error_msg)
        elif guidance != model_config["recommended_guidance"]:
            recommendations.append(f"Recommended guidance for {model}: {model_config['recommended_guidance']}")

        # Validate steps
        steps_min, steps_max = model_config["steps_range"]
        if steps < steps_min or steps > steps_max:
            error_msg = (
                f"Steps {steps} is outside recommended range ({steps_min}-{steps_max}) for {model}"
            )
            if auto_correct and hard:
                corrected_steps = max(steps_min, min(steps, steps_max))
                corrected_values["steps"] = corrected_steps
                warnings.append(f"{error_msg}. Auto-corrected to {corrected_steps}")
            elif auto_correct and not hard:
                # Absolute safety only: 1–100. Quality presets may go above recommended.
                abs_lo, abs_hi = 1, 100
                if steps < abs_lo or steps > abs_hi:
                    corrected_values["steps"] = max(abs_lo, min(steps, abs_hi))
                    warnings.append(
                        f"Steps {steps} clamped to absolute safety bounds [{abs_lo}, {abs_hi}]"
                    )
                else:
                    warnings.append(
                        f"{error_msg}. Left as-is (quality slider owns this); "
                        f"recommended={model_config['recommended_steps']}"
                    )
            else:
                warnings.append(error_msg)
        elif steps != model_config["recommended_steps"]:
            recommendations.append(f"Recommended steps for {model}: {model_config['recommended_steps']}")

        # Validate dimensions (family max side + max area via shared helper)
        min_w, min_h = model_config["min_dimensions"]
        max_w, max_h = model_config.get("max_dimensions", (2048, 2048))

        if width < min_w or height < min_h:
            error_msg = f"Dimensions {width}x{height} are below minimum {min_w}x{min_h} for {model}"
            if auto_correct:
                corrected_width = max(min_w, width)
                corrected_height = max(min_h, height)
                corrected_values["width"] = corrected_width
                corrected_values["height"] = corrected_height
                warnings.append(f"{error_msg}. Auto-corrected to {corrected_width}x{corrected_height}")
            else:
                errors.append(error_msg)
        else:
            try:
                from backend.services.image_resolution_limits import clamp_image_dimensions
                cw, ch, dim_warns = clamp_image_dimensions(width, height, model)
                for msg in dim_warns:
                    warnings.append(msg)
                # Soft models (hard_clamp=False): only apply area/side clamp when over limit
                if (cw, ch) != (width, height):
                    if hard:
                        corrected_values["width"] = cw
                        corrected_values["height"] = ch
                        warnings.append(
                            f"Dimensions {width}x{height} clamped to {cw}x{ch} for {model}"
                        )
                    else:
                        # Still apply hard safety clamp for absurd sizes (Flux 4MP, etc.)
                        max_pixels = model_config.get("max_pixels")
                        over_side = width > max_w or height > max_h
                        over_area = max_pixels is not None and (width * height) > int(max_pixels)
                        if over_side or over_area:
                            corrected_values["width"] = cw
                            corrected_values["height"] = ch
                            warnings.append(
                                f"Dimensions {width}x{height} exceed {model} limits; "
                                f"clamped to {cw}x{ch}"
                            )
            except Exception as e:
                logger.warning(f"dimension clamp helper failed: {e}")
                if width > max_w or height > max_h:
                    warnings.append(
                        f"Dimensions {width}x{height} exceed recommended maximum "
                        f"{max_w}x{max_h} for {model}."
                    )

        # Check for recommended dimensions
        rec_w, rec_h = model_config["recommended_dimensions"]
        if width != rec_w or height != rec_h:
            recommendations.append(f"Recommended dimensions for {model}: {rec_w}x{rec_h}")

        # Add model-specific warnings
        for warning in model_config.get("warnings", []):
            warnings.append(f"{model}: {warning}")

        # For zimage-turbo the guidance warning is intentionally omitted from the static list
        # (see MODEL_SETTINGS) because Turbo is CFG-distilled (recommended 0.0); the Batch UI
        # and soft-clamp keep values in [0, 2]. We still warn via the dynamic check below if a
        # high value is supplied.

        # Check for common issues
        if "turbo" in model.lower() and guidance > 1.0:
            warnings.append(f"Turbo models ({model}) don't use guidance scale effectively. Consider setting to 0.0-1.0")

        if width != height and "xl" in model.lower():
            recommendations.append("SDXL models work best with square dimensions (1024x1024)")

        is_valid = len(errors) == 0

        return ValidationResult(
            is_valid=is_valid,
            warnings=warnings,
            errors=errors,
            corrected_values=corrected_values,
            recommendations=recommendations
        )

    def get_model_recommendations(self, model: str) -> Dict[str, Any]:
        """Get recommended settings for a model."""
        model_config = self.model_settings.get(model)
        if not model_config:
            model_config = self.model_settings["sd-1.5"]

        return {
            "guidance": model_config["recommended_guidance"],
            "steps": model_config["recommended_steps"],
            "width": model_config["recommended_dimensions"][0],
            "height": model_config["recommended_dimensions"][1],
            "best_for": model_config["best_for"],
            "warnings": model_config.get("warnings", [])
        }

    def get_model_info(self, model: str) -> Dict[str, Any]:
        """Get full model configuration."""
        return self.model_settings.get(model, self.model_settings["sd-1.5"])

    def get_all_models(self) -> List[str]:
        """Get list of all supported models."""
        return list(self.model_settings.keys())


# Singleton instance
_validator_instance = None

def get_settings_validator() -> SettingsValidator:
    """Get singleton settings validator instance."""
    global _validator_instance
    if _validator_instance is None:
        _validator_instance = SettingsValidator()
    return _validator_instance

