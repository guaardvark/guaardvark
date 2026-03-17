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


# Model-specific settings configuration
MODEL_SETTINGS = {
    "sd-xl": {
        "guidance_range": (4.0, 9.0),
        "recommended_guidance": 7.0,
        "min_dimensions": (768, 768),
        "recommended_dimensions": (1024, 1024),
        "steps_range": (20, 40),
        "recommended_steps": 25,
        "best_for": ["high_res", "anatomy", "landscapes"],
        "warnings": ["Guidance > 9.0 causes black images"],
        "max_dimensions": (1536, 1536),
    },
    "sdxl-turbo": {
        "guidance_range": (0.0, 1.0),
        "recommended_guidance": 0.0,
        "min_dimensions": (768, 768),
        "recommended_dimensions": (1024, 1024),
        "steps_range": (1, 4),
        "recommended_steps": 4,
        "best_for": ["speed", "previews", "high_res"],
        "warnings": [
            "Not for final quality images",
            "Guidance not used by turbo models",
        ],
        "max_dimensions": (1536, 1536),
    },
    "sd-1.5": {
        "guidance_range": (1.0, 15.0),
        "recommended_guidance": 7.5,
        "min_dimensions": (512, 512),
        "recommended_dimensions": (512, 512),
        "steps_range": (10, 50),
        "recommended_steps": 20,
        "best_for": ["general", "speed", "reliability"],
        "warnings": [],
        "max_dimensions": (768, 768),
    },
    "sd-2.1": {
        "guidance_range": (7.0, 12.0),
        "recommended_guidance": 7.5,
        "min_dimensions": (512, 512),
        "recommended_dimensions": (768, 768),
        "steps_range": (25, 50),
        "recommended_steps": 30,
        "best_for": ["general", "anatomy"],
        "warnings": [],
        "max_dimensions": (768, 768),
    },
    "realistic-vision": {
        "guidance_range": (7.0, 10.0),
        "recommended_guidance": 8.0,
        "min_dimensions": (512, 512),
        "recommended_dimensions": (512, 768),  # Best for portraits
        "steps_range": (25, 40),
        "recommended_steps": 30,
        "best_for": ["faces", "portraits", "photorealism"],
        "warnings": [],
        "max_dimensions": (768, 768),
    },
    "epic-realism": {
        "guidance_range": (7.0, 9.0),
        "recommended_guidance": 7.5,
        "min_dimensions": (512, 512),
        "recommended_dimensions": (512, 768),
        "steps_range": (30, 40),
        "recommended_steps": 35,
        "best_for": ["faces", "portraits", "cinematic"],
        "warnings": [],
        "max_dimensions": (768, 768),
    },
    "deliberate": {
        "guidance_range": (7.0, 10.0),
        "recommended_guidance": 7.5,
        "min_dimensions": (512, 512),
        "recommended_dimensions": (512, 512),
        "steps_range": (20, 35),
        "recommended_steps": 25,
        "best_for": ["artistic", "realistic", "versatile"],
        "warnings": [],
        "max_dimensions": (768, 768),
    },
    "dreamlike": {
        "guidance_range": (7.0, 10.0),
        "recommended_guidance": 7.5,
        "min_dimensions": (512, 512),
        "recommended_dimensions": (512, 512),
        "steps_range": (20, 35),
        "recommended_steps": 25,
        "best_for": ["photorealism", "artistic"],
        "warnings": [],
        "max_dimensions": (768, 768),
    },
    "sd-turbo": {
        "guidance_range": (0.0, 1.0),
        "recommended_guidance": 0.0,
        "min_dimensions": (512, 512),
        "recommended_dimensions": (512, 512),
        "steps_range": (1, 4),
        "recommended_steps": 4,
        "best_for": ["speed", "previews"],
        "warnings": [
            "Not for final quality images",
            "Guidance not used by turbo models",
        ],
        "max_dimensions": (512, 512),
    },
    "openjourney": {
        "guidance_range": (7.0, 10.0),
        "recommended_guidance": 7.5,
        "min_dimensions": (512, 512),
        "recommended_dimensions": (512, 512),
        "steps_range": (20, 35),
        "recommended_steps": 25,
        "best_for": ["artistic", "midjourney_style"],
        "warnings": [],
        "max_dimensions": (768, 768),
    },
    "analog": {
        "guidance_range": (7.0, 10.0),
        "recommended_guidance": 7.5,
        "min_dimensions": (512, 512),
        "recommended_dimensions": (512, 512),
        "steps_range": (20, 35),
        "recommended_steps": 25,
        "best_for": ["film_photography", "aesthetic"],
        "warnings": [],
        "max_dimensions": (768, 768),
    },
    "anything-v3": {
        "guidance_range": (7.0, 10.0),
        "recommended_guidance": 7.5,
        "min_dimensions": (512, 512),
        "recommended_dimensions": (512, 512),
        "steps_range": (20, 35),
        "recommended_steps": 25,
        "best_for": ["anime", "illustration"],
        "warnings": [],
        "max_dimensions": (768, 768),
    },
}


class SettingsValidator:
    """Validates image generation settings with model-specific rules."""

    def __init__(self):
        self.model_settings = MODEL_SETTINGS

    def validate_settings(
        self,
        model: str,
        guidance: float,
        steps: int,
        width: int,
        height: int,
        auto_correct: bool = True,
    ) -> ValidationResult:
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
        guidance_min, guidance_max = model_config["guidance_range"]
        if guidance < guidance_min or guidance > guidance_max:
            error_msg = f"Guidance scale {guidance} is outside valid range ({guidance_min}-{guidance_max}) for {model}"
            if auto_correct:
                corrected_guidance = max(guidance_min, min(guidance, guidance_max))
                corrected_values["guidance"] = corrected_guidance
                warnings.append(f"{error_msg}. Auto-corrected to {corrected_guidance}")
            else:
                errors.append(error_msg)
        elif guidance != model_config["recommended_guidance"]:
            recommendations.append(
                f"Recommended guidance for {model}: {model_config['recommended_guidance']}"
            )

        # Validate steps
        steps_min, steps_max = model_config["steps_range"]
        if steps < steps_min or steps > steps_max:
            error_msg = f"Steps {steps} is outside recommended range ({steps_min}-{steps_max}) for {model}"
            if auto_correct:
                corrected_steps = max(steps_min, min(steps, steps_max))
                corrected_values["steps"] = corrected_steps
                warnings.append(f"{error_msg}. Auto-corrected to {corrected_steps}")
            else:
                warnings.append(error_msg)
        elif steps != model_config["recommended_steps"]:
            recommendations.append(
                f"Recommended steps for {model}: {model_config['recommended_steps']}"
            )

        # Validate dimensions
        min_w, min_h = model_config["min_dimensions"]
        max_w, max_h = model_config.get("max_dimensions", (2048, 2048))

        if width < min_w or height < min_h:
            error_msg = f"Dimensions {width}x{height} are below minimum {min_w}x{min_h} for {model}"
            if auto_correct:
                corrected_width = max(min_w, width)
                corrected_height = max(min_h, height)
                corrected_values["width"] = corrected_width
                corrected_values["height"] = corrected_height
                warnings.append(
                    f"{error_msg}. Auto-corrected to {corrected_width}x{corrected_height}"
                )
            else:
                errors.append(error_msg)
        elif width > max_w or height > max_h:
            warning_msg = f"Dimensions {width}x{height} exceed recommended maximum {max_w}x{max_h} for {model}. May cause quality issues or out of memory."
            warnings.append(warning_msg)

        # Check for recommended dimensions
        rec_w, rec_h = model_config["recommended_dimensions"]
        if width != rec_w or height != rec_h:
            recommendations.append(
                f"Recommended dimensions for {model}: {rec_w}x{rec_h}"
            )

        # Add model-specific warnings
        for warning in model_config.get("warnings", []):
            warnings.append(f"{model}: {warning}")

        # Check for common issues
        if "turbo" in model.lower() and guidance > 1.0:
            warnings.append(
                f"Turbo models ({model}) don't use guidance scale effectively. Consider setting to 0.0-1.0"
            )

        if width != height and "xl" in model.lower():
            recommendations.append(
                "SDXL models work best with square dimensions (1024x1024)"
            )

        is_valid = len(errors) == 0

        return ValidationResult(
            is_valid=is_valid,
            warnings=warnings,
            errors=errors,
            corrected_values=corrected_values,
            recommendations=recommendations,
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
            "warnings": model_config.get("warnings", []),
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
