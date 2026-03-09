# backend/services/model_recommender.py
# Model Recommender Service - Recommends best models based on prompt content analysis

import logging
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class ModelRecommendation:
    """Model recommendation with reasoning"""
    model: str
    score: float
    reasoning: List[str]
    recommended_settings: Dict[str, Any]

class ModelRecommender:
    """Recommends models based on prompt content analysis."""

    def __init__(self):
        # Model quality ratings (0-5 scale)
        self.model_ratings = {
            "realistic-vision": {
                "face_quality": 5,
                "anatomy": 5,
                "photorealism": 5,
                "speed": 3,
                "best_for": ["faces", "portraits", "people", "photorealism"]
            },
            "epic-realism": {
                "face_quality": 5,
                "anatomy": 5,
                "photorealism": 5,
                "speed": 3,
                "best_for": ["faces", "portraits", "cinematic", "professional"]
            },
            "sd-xl": {
                "face_quality": 4,
                "anatomy": 5,
                "photorealism": 4,
                "speed": 2,
                "best_for": ["high_res", "anatomy", "landscapes", "full_body"]
            },
            "deliberate": {
                "face_quality": 4,
                "anatomy": 4,
                "photorealism": 4,
                "speed": 3,
                "best_for": ["artistic", "realistic", "versatile"]
            },
            "dreamlike": {
                "face_quality": 4,
                "anatomy": 4,
                "photorealism": 4,
                "speed": 3,
                "best_for": ["photorealism", "artistic"]
            },
            "sd-2.1": {
                "face_quality": 3,
                "anatomy": 4,
                "photorealism": 3,
                "speed": 3,
                "best_for": ["general", "anatomy"]
            },
            "sd-1.5": {
                "face_quality": 2,
                "anatomy": 2,
                "photorealism": 2,
                "speed": 4,
                "best_for": ["general", "speed", "reliability"]
            },
            "sd-turbo": {
                "face_quality": 2,
                "anatomy": 2,
                "photorealism": 2,
                "speed": 5,
                "best_for": ["speed", "previews"]
            },
            "sdxl-turbo": {
                "face_quality": 3,
                "anatomy": 4,
                "photorealism": 3,
                "speed": 4,
                "best_for": ["speed", "high_res", "previews"]
            },
            "openjourney": {
                "face_quality": 3,
                "anatomy": 3,
                "photorealism": 3,
                "speed": 3,
                "best_for": ["artistic", "midjourney_style"]
            },
            "analog": {
                "face_quality": 3,
                "anatomy": 3,
                "photorealism": 3,
                "speed": 3,
                "best_for": ["film_photography", "aesthetic"]
            },
            "anything-v3": {
                "face_quality": 2,
                "anatomy": 2,
                "photorealism": 1,
                "speed": 3,
                "best_for": ["anime", "illustration"]
            }
        }

    def recommend_models(self, 
                        detection: Dict[str, Any],
                        prioritize_speed: bool = False,
                        prioritize_quality: bool = True) -> List[ModelRecommendation]:
        """
        Recommend models based on content detection.
        
        Args:
            detection: Content detection results from prompt analysis
            prioritize_speed: If True, prioritize faster models
            prioritize_quality: If True, prioritize higher quality models
            
        Returns:
            List of ModelRecommendation objects, sorted by score (highest first)
        """
        recommendations = []

        # Determine what we're looking for
        has_person = detection.get("has_person", False)
        has_face = detection.get("has_face", False)
        has_hands = detection.get("has_hands", False)
        has_action = detection.get("has_action", False)
        needs_high_res = detection.get("needs_high_res", False)

        # Score each model
        for model, ratings in self.model_ratings.items():
            score = 0.0
            reasoning = []

            # Face quality is critical if faces are present
            if has_face or has_person:
                face_score = ratings["face_quality"] * 2.0  # Double weight for faces
                score += face_score
                if face_score >= 8:
                    reasoning.append("Excellent for faces and portraits")
                elif face_score >= 6:
                    reasoning.append("Good for faces")
                else:
                    reasoning.append("Not ideal for faces")

            # Anatomy is important for people
            if has_person:
                anatomy_score = ratings["anatomy"] * 1.5
                score += anatomy_score
                if anatomy_score >= 6:
                    reasoning.append("Excellent anatomy quality")
                elif anatomy_score >= 4:
                    reasoning.append("Good anatomy")

            # High resolution for detailed scenes
            if needs_high_res or (has_person and not prioritize_speed):
                if "xl" in model.lower():
                    score += 2.0
                    reasoning.append("High resolution output (1024x1024+)")
                elif ratings.get("photorealism", 0) >= 4:
                    score += 1.0
                    reasoning.append("Good detail quality")

            # Speed penalty/bonus
            if prioritize_speed:
                speed_bonus = ratings["speed"] * 0.5
                score += speed_bonus
                if speed_bonus >= 2:
                    reasoning.append("Fast generation")
            else:
                # Slight penalty for very slow models if not prioritizing quality
                if ratings["speed"] <= 2 and not prioritize_quality:
                    score -= 0.5

            # Best for matching
            best_for = ratings.get("best_for", [])
            matches = []
            if has_face and "faces" in best_for:
                matches.append("faces")
            if has_person and "people" in best_for:
                matches.append("people")
            if has_action and "versatile" in best_for:
                matches.append("action scenes")
            
            if matches:
                score += len(matches) * 0.5
                reasoning.append(f"Optimized for: {', '.join(matches)}")

            # Photorealism bonus
            if detection.get("style") == "realistic" or not detection.get("style"):
                photo_score = ratings["photorealism"] * 0.5
                score += photo_score

            recommendations.append(ModelRecommendation(
                model=model,
                score=score,
                reasoning=reasoning if reasoning else ["General purpose model"],
                recommended_settings=self._get_recommended_settings(model, detection)
            ))

        # Sort by score (highest first)
        recommendations.sort(key=lambda x: x.score, reverse=True)

        return recommendations

    def _get_recommended_settings(self, model: str, detection: Dict[str, Any]) -> Dict[str, Any]:
        """Get recommended settings for a model based on content."""
        from backend.services.settings_validator import get_settings_validator
        
        try:
            validator = get_settings_validator()
            model_info = validator.get_model_info(model)
            recommendations = validator.get_model_recommendations(model)
            
            settings = {
                "guidance": recommendations["guidance"],
                "steps": recommendations["steps"],
                "width": recommendations["width"],
                "height": recommendations["height"]
            }
            
            # Adjust based on content type
            if detection.get("has_face") or detection.get("has_person"):
                # For faces, prefer portrait orientation
                if model in ["realistic-vision", "epic-realism"]:
                    settings["height"] = 768
                    settings["width"] = 512
                    settings["steps"] = max(settings["steps"], 30)
            
            return settings
        except Exception as e:
            logger.warning(f"Could not get recommended settings: {e}")
            return {
                "guidance": 7.5,
                "steps": 20,
                "width": 512,
                "height": 512
            }

    def get_model_comparison(self, models: List[str] = None) -> Dict[str, Dict[str, Any]]:
        """Get comparison of model qualities."""
        if models is None:
            models = list(self.model_ratings.keys())
        
        comparison = {}
        for model in models:
            if model in self.model_ratings:
                comparison[model] = self.model_ratings[model].copy()
        
        return comparison


# Singleton instance
_recommender_instance = None

def get_model_recommender() -> ModelRecommender:
    """Get singleton model recommender instance."""
    global _recommender_instance
    if _recommender_instance is None:
        _recommender_instance = ModelRecommender()
    return _recommender_instance

