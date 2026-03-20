
import logging
import os
import uuid
import time
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
import tempfile
import threading

logger = logging.getLogger(__name__)

try:
    import torch
    from diffusers import (
        StableDiffusionPipeline,
        StableDiffusionXLPipeline,
        StableDiffusionImg2ImgPipeline,
        DPMSolverMultistepScheduler
    )
    from PIL import Image
    import safetensors
    diffusion_available = True
    logger.info("Diffusion dependencies loaded successfully")
except ImportError as e:
    diffusion_available = False
    logger.warning(f"Diffusion dependencies not available: {e}")

try:
    from backend.config import CACHE_DIR
    config_available = True
except ImportError:
    config_available = False
    CACHE_DIR = "/tmp/guaardvark_cache"

try:
    from backend.services.face_restoration_service import get_face_restoration_service
    face_restoration_available = True
except ImportError as e:
    face_restoration_available = False
    logger.warning(f"Face restoration service not available: {e}")

@dataclass
class ImageGenerationRequest:
    prompt: str
    negative_prompt: str = ""
    width: int = 512
    height: int = 512
    num_inference_steps: int = 20
    guidance_scale: float = 7.5
    style: str = "realistic"
    seed: Optional[int] = None
    model: str = "sd-1.5"
    content_preset: Optional[str] = None
    auto_enhance: bool = True
    enhance_anatomy: bool = True
    enhance_faces: bool = True
    enhance_hands: bool = True
    restore_faces: bool = True
    face_restoration_weight: float = 0.5

@dataclass
class ImageGenerationResult:
    success: bool
    image_path: Optional[str] = None
    image_data: Optional[bytes] = None
    prompt_used: str = ""
    negative_prompt_used: str = ""
    model_used: str = ""
    generation_time: float = 0.0
    image_size: Tuple[int, int] = (512, 512)
    seed_used: Optional[int] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = None

class OfflineImageGenerator:

    def __init__(self):
        project_root = Path(__file__).parent.parent.parent
        self.models_dir = project_root / "data" / "models" / "stable_diffusion"
        self.cache_dir = Path(CACHE_DIR) / "generated_images"

        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.default_model = "runwayml/stable-diffusion-v1-5"
        self.available_models = {
            "sd-1.5": "runwayml/stable-diffusion-v1-5",
            "sd-2.1": "stabilityai/stable-diffusion-2-1",
            "sd-xl": "stabilityai/stable-diffusion-xl-base-1.0",

            "dreamlike": "dreamlike-art/dreamlike-photoreal-2.0",
            "deliberate": "XpucT/Deliberate",
            "realistic-vision": "SG161222/Realistic_Vision_V5.1_noVAE",
            "epic-realism": "emilianJR/epiCRealism",

            "sd-turbo": "stabilityai/sd-turbo",
            "sdxl-turbo": "stabilityai/sdxl-turbo",

            "openjourney": "prompthero/openjourney",
            "analog": "wavymulder/Analog-Diffusion",

            "anything-v3": "Linaqruf/anything-v3.0"
        }

        self.anatomy_negative = "deformed body, distorted anatomy, extra limbs, missing limbs, extra arms, missing arms, extra legs, missing legs, fused limbs, disconnected limbs, floating limbs, asymmetrical body, disproportionate limbs, twisted torso, broken spine, impossible pose, malformed body, mutated anatomy, gross proportions, extra heads, conjoined, siamese, bad anatomy, cropped body, out of frame body, duplicate person, clone"

        self.face_negative = "asymmetrical face, lopsided face, distorted facial features, bad teeth, cross-eyed, lazy eye, eyes looking different directions, uneven eyes, floating eyes, deformed face, malformed face, poorly drawn eyes, poorly drawn nose, poorly drawn mouth, missing eyes, extra eyes, blurry face, low quality face, ugly face"

        self.hands_negative = "bad hands, deformed hands, malformed hands, extra fingers, missing fingers, fused fingers, webbed fingers, too many fingers, wrong number of fingers, six fingers, four fingers, three fingers, mutant hands, claw hands, backwards hands, wrong hand orientation, floating hands, disconnected hands, hands with no wrist, poorly drawn hands"

        self.body_negative = "wrong proportions, head too big, head too small, torso too long, arms too long, arms too short, legs too long, legs too short, unnatural stance, impossible posture, broken joints, dislocated joints, reverse joints"

        self.logic_negative = "floating objects, disconnected elements, impossible physics, wrong perspective, incorrect scale, illogical scene, inconsistent lighting, impossible poses, wrong object placement"

        self.base_negative = "low quality, blurry, distorted, watermark, signature, text, low resolution, pixelated, artifacts, noise, oversaturated, jpeg artifacts"

        self.style_configs = {
            "realistic": {
                "positive_suffix": "photorealistic, high quality, detailed, sharp focus, professional photography, natural lighting, realistic textures, correct proportions",
                "negative_prompt": f"cartoon, anime, illustration, painting, drawing, art, sketch, 3d render, cgi, {self.anatomy_negative}, {self.base_negative}"
            },
            "artistic": {
                "positive_suffix": "artistic, beautiful, creative, masterpiece, fine art, professional artwork, balanced composition, artistic lighting",
                "negative_prompt": f"amateur, {self.anatomy_negative}, {self.base_negative}"
            },
            "cartoon": {
                "positive_suffix": "cartoon style, animated, colorful, clean lines, cel shading, vector illustration, flat design, geometric forms",
                "negative_prompt": f"realistic, photographic, {self.base_negative}"
            },
            "sketch": {
                "positive_suffix": "pencil sketch, hand-drawn, artistic lines, monochrome, detailed linework, professional illustration",
                "negative_prompt": f"colored, photographic, {self.base_negative}"
            },
            "infographic": {
                "positive_suffix": "flat vector illustration, infographic style, clean geometric forms, minimal shadows, professional design, clear composition, no people",
                "negative_prompt": f"photorealism, realistic faces, realistic people, {self.base_negative}"
            },
            "technical": {
                "positive_suffix": "technical illustration, clean lines, precise details, professional diagram, clear composition, minimal style",
                "negative_prompt": f"artistic, {self.base_negative}"
            }
        }

        self.content_presets = {
            "person_portrait": {
                "positive_suffix": "professional portrait photography, natural skin texture, realistic lighting, sharp focus on face, proper facial proportions, symmetrical features",
                "negative_prompt": f"{self.anatomy_negative}, {self.face_negative}, {self.base_negative}",
                "recommended_steps": 30,
                "recommended_guidance": 7.5,
                "recommended_dimensions": (512, 768)
            },
            "person_full_body": {
                "positive_suffix": "full body shot, proper human proportions, natural pose, correct anatomy, realistic stance, balanced composition, anatomically correct",
                "negative_prompt": f"{self.anatomy_negative}, {self.hands_negative}, {self.body_negative}, {self.logic_negative}, floating limbs, disconnected body parts, {self.base_negative}",
                "recommended_steps": 35,
                "recommended_guidance": 8.0,
                "recommended_dimensions": (512, 768)
            },
            "person_athletic": {
                "positive_suffix": "athletic activity, natural movement, dynamic pose, proper body mechanics, focused action, correct body proportions",
                "negative_prompt": f"{self.anatomy_negative}, {self.hands_negative}, {self.body_negative}, {self.logic_negative}, stiff pose, unnatural stance, {self.base_negative}",
                "recommended_steps": 30,
                "recommended_guidance": 7.5,
                "recommended_dimensions": (768, 512)
            },
            "person_working": {
                "positive_suffix": "realistic work scene, natural work pose, logical workspace, proper body posture",
                "negative_prompt": f"{self.anatomy_negative}, {self.hands_negative}, {self.body_negative}, {self.logic_negative}, floating tools, disconnected actions, impossible poses, {self.base_negative}",
                "recommended_steps": 35,
                "recommended_guidance": 8.0,
                "recommended_dimensions": (768, 512)
            },
            "product_photo": {
                "positive_suffix": "product photography, clean background, studio lighting, commercial quality, sharp focus, professional presentation",
                "negative_prompt": f"blurry, distorted, {self.base_negative}",
                "recommended_steps": 25,
                "recommended_guidance": 7.0,
                "recommended_dimensions": (512, 512)
            },
            "landscape": {
                "positive_suffix": "landscape photography, scenic, natural lighting, high dynamic range, beautiful composition, vivid colors",
                "negative_prompt": f"blurry, oversaturated, artificial, {self.base_negative}",
                "recommended_steps": 25,
                "recommended_guidance": 7.0,
                "recommended_dimensions": (768, 512)
            },
            "infographic_preset": {
                "positive_suffix": "flat vector design, clean geometric shapes, minimal design, professional infographic, clear icons, simple composition",
                "negative_prompt": f"photorealistic, 3d, shadows, gradients, complex textures, realistic people, {self.base_negative}",
                "recommended_steps": 20,
                "recommended_guidance": 7.5,
                "recommended_dimensions": (768, 768)
            },
            "general": {
                "positive_suffix": "high quality, detailed, professional, sharp focus",
                "negative_prompt": f"{self.base_negative}",
                "recommended_steps": 20,
                "recommended_guidance": 7.5,
                "recommended_dimensions": (512, 512)
            }
        }

        self._pipeline = None
        self._img2img_pipeline = None
        self._current_model = None
        
        self._device = "cpu"
        if torch.cuda.is_available():
            try:
                dummy = torch.zeros(1, device='cuda')
                _ = dummy + dummy
                torch.cuda.synchronize()
                self._device = "cuda"
            except Exception as e:
                logger.warning(f"CUDA is available but not usable (e.g., PyTorch compatibility issue), falling back to CPU: {e}")
        
        self._generation_lock = threading.Lock()

        self._compile_failed = False
        self._compile_unet_orig = None
        self._compile_vae_orig = None

        self.service_available = diffusion_available

        logger.info(f"OfflineImageGenerator initialized - Device: {self._device}, Models dir: {self.models_dir}")

    def _get_model_path(self, model_id: str) -> Path:
        model_name = model_id.replace("/", "--")
        return self.models_dir / model_name

    def _is_model_downloaded(self, model_id: str) -> bool:
        model_path = self._get_model_path(model_id)
        return model_path.exists() and any(model_path.iterdir())

    def _download_model(self, model_id: str) -> bool:
        if not self.service_available:
            logger.error("Diffusion service not available for model download")
            return False

        try:
            model_path = self._get_model_path(model_id)
            logger.info(f"Downloading model {model_id} to {model_path}")

            is_sdxl = 'xl' in model_id.lower() or 'sdxl' in model_id.lower()

            pipeline_class = StableDiffusionXLPipeline if is_sdxl else StableDiffusionPipeline

            load_kwargs = {
                "torch_dtype": torch.float16 if self._device == "cuda" else torch.float32,
            }

            if not is_sdxl:
                load_kwargs["safety_checker"] = None
                load_kwargs["requires_safety_checker"] = False

            logger.info(f"Downloading with {pipeline_class.__name__} (SDXL: {is_sdxl})")

            pipeline = pipeline_class.from_pretrained(
                model_id,
                **load_kwargs
            )

            pipeline.save_pretrained(model_path)

            del pipeline
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            logger.info(f"Model {model_id} downloaded successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to download model {model_id}: {e}")
            return False

    def _load_pipeline(self, model_id: str) -> bool:
        if not self.service_available:
            return False

        try:
            if self._pipeline and self._current_model == model_id:
                return True

            if self._pipeline:
                del self._pipeline
                torch.cuda.empty_cache() if torch.cuda.is_available() else None

            if not self._is_model_downloaded(model_id):
                logger.info(f"Model {model_id} not found locally, downloading...")
                if not self._download_model(model_id):
                    return False

            model_path = self._get_model_path(model_id)

            is_sdxl = 'xl' in model_id.lower() or 'sdxl' in model_id.lower()

            pipeline_class = StableDiffusionXLPipeline if is_sdxl else StableDiffusionPipeline
            logger.info(f"Loading model with {pipeline_class.__name__} (SDXL: {is_sdxl})")

            load_kwargs = {
                "torch_dtype": torch.float16 if self._device == "cuda" else torch.float32,
            }

            if not is_sdxl:
                load_kwargs["safety_checker"] = None
                load_kwargs["requires_safety_checker"] = False

            self._pipeline = pipeline_class.from_pretrained(
                model_path,
                **load_kwargs
            )

            self._pipeline.scheduler = DPMSolverMultistepScheduler.from_config(
                self._pipeline.scheduler.config
            )

            self._pipeline = self._pipeline.to(self._device)

            if hasattr(self._pipeline, "enable_attention_slicing"):
                self._pipeline.enable_attention_slicing()

            if hasattr(self._pipeline, "enable_xformers_memory_efficient_attention"):
                try:
                    self._pipeline.enable_xformers_memory_efficient_attention()
                    logger.info("Enabled xformers memory efficient attention")
                except Exception as e:
                    logger.warning(f"Failed to enable xformers memory efficient attention: {e}")

            if hasattr(self._pipeline, "enable_vae_slicing"):
                self._pipeline.enable_vae_slicing()
                logger.info("Enabled VAE slicing")

            if hasattr(self._pipeline, "enable_vae_tiling"):
                self._pipeline.enable_vae_tiling()
                logger.info("Enabled VAE tiling")


            if hasattr(torch, 'compile') and self._device == "cuda" and not self._compile_failed:
                try:
                    if hasattr(self._pipeline, 'unet'):
                        self._compile_unet_orig = self._pipeline.unet
                        self._pipeline.unet = torch.compile(self._pipeline.unet, mode="reduce-overhead")
                        logger.info("Enabled torch.compile for UNet")

                    if hasattr(self._pipeline, 'vae'):
                        self._compile_vae_orig = self._pipeline.vae
                        self._pipeline.vae = torch.compile(self._pipeline.vae, mode="reduce-overhead")
                        logger.info("Enabled torch.compile for VAE")
                except Exception as e:
                    logger.warning(f"Failed to enable torch.compile: {e}")
                    self._compile_unet_orig = None
                    self._compile_vae_orig = None

            self._current_model = model_id
            logger.info(f"Pipeline loaded successfully with model {model_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to load pipeline with model {model_id}: {e}")
            self._pipeline = None
            self._current_model = None
            return False

    def _detect_subject_count(self, prompt: str) -> Dict[str, Any]:
        prompt_lower = prompt.lower()

        single_indicators = ['a ', 'an ', 'one ', 'single ', 'solo ']
        multiple_indicators = ['two ', 'three ', 'four ', 'multiple ', 'several ', 'many ', 'group of ', 'couple ', 'pair of ']

        has_single = any(indicator in prompt_lower for indicator in single_indicators)
        has_multiple = any(indicator in prompt_lower for indicator in multiple_indicators)

        person_plurals = ['men', 'women', 'people', 'workers', 'builders', 'chefs', 'doctors',
                         'teachers', 'children', 'boys', 'girls', 'employees', 'professionals']
        has_plural_subject = any(plural in prompt_lower for plural in person_plurals)

        person_singulars = ['man', 'woman', 'person', 'child', 'boy', 'girl']
        has_and_conjunction = False
        if ' and ' in prompt_lower:
            words_around_and = []
            for singular in person_singulars:
                if singular in prompt_lower:
                    words_around_and.append(singular)
            if len(words_around_and) > 1 and ' and ' in prompt_lower:
                has_and_conjunction = True

        if has_multiple or has_plural_subject or has_and_conjunction:
            subject_count = "multiple"
        elif has_single:
            subject_count = "single"
        else:
            subject_count = "single"

        return {
            "subject_count": subject_count,
            "is_single_subject": subject_count == "single",
            "is_multiple_subjects": subject_count == "multiple"
        }

    def detect_content_type(self, prompt: str) -> Dict[str, Any]:
        prompt_lower = prompt.lower()

        detection = {
            "has_person": False,
            "has_face": False,
            "has_hands": False,
            "has_action": False,
            "has_interaction": False,
            "has_spatial": False,
            "detected_actions": [],
            "recommended_preset": "general",
            "warnings": [],
            "subject_count_info": {}
        }

        detection["subject_count_info"] = self._detect_subject_count(prompt)

        person_words = ['man', 'woman', 'person', 'people', 'worker', 'builder', 'chef', 'doctor',
                       'teacher', 'child', 'boy', 'girl', 'human', 'employee', 'staff', 'professional',
                       'craftsman', 'mechanic', 'plumber', 'electrician', 'carpenter', 'painter']
        if any(word in prompt_lower for word in person_words):
            detection["has_person"] = True

        face_words = ['portrait', 'face', 'headshot', 'selfie', 'close-up', 'closeup', 'head shot']
        if any(word in prompt_lower for word in face_words):
            detection["has_face"] = True

        hand_words = ['hand', 'holding', 'grabbing', 'gripping', 'carrying', 'lifting', 'pointing',
                     'touching', 'typing', 'writing', 'drawing', 'using']
        if any(word in prompt_lower for word in hand_words):
            detection["has_hands"] = True

        action_map = {
            'building': ['building', 'constructing', 'assembling', 'installing', 'fixing', 'repairing'],
            'working': ['working', 'operating', 'using', 'handling'],
            'cooking': ['cooking', 'baking', 'preparing food', 'chef', 'kitchen'],
            'driving': ['driving', 'steering', 'riding', 'in car', 'behind wheel'],
            'typing': ['typing', 'at computer', 'at keyboard', 'coding', 'programming'],
            'reading': ['reading', 'studying', 'with book', 'looking at'],
            'sports': ['playing', 'running', 'jumping', 'swimming', 'exercising', 'training', 'jogging', 'treadmill', 'workout'],
            'gardening': ['gardening', 'planting', 'watering', 'pruning', 'mowing']
        }

        for action_type, keywords in action_map.items():
            if any(keyword in prompt_lower for keyword in keywords):
                detection["has_action"] = True
                detection["detected_actions"].append(action_type)

        interaction_words = ['with', 'using', 'holding', 'beside', 'operating', 'gripping', 'manipulating']
        if detection["has_person"] and any(word in prompt_lower for word in interaction_words):
            detection["has_interaction"] = True

        spatial_words = ['next to', 'behind', 'in front of', 'beside', 'between', 'under', 'over',
                        'sitting on', 'standing by', 'leaning against', 'near']
        if any(word in prompt_lower for word in spatial_words):
            detection["has_spatial"] = True

        if detection["has_face"] and detection["has_person"]:
            detection["recommended_preset"] = "person_portrait"
        elif detection["has_person"] and 'sports' in detection["detected_actions"]:
            detection["recommended_preset"] = "person_athletic"
        elif detection["has_person"] and detection["has_action"]:
            detection["recommended_preset"] = "person_working"
        elif detection["has_person"]:
            detection["recommended_preset"] = "person_full_body"
        elif any(word in prompt_lower for word in ['landscape', 'scenery', 'nature', 'mountain', 'beach', 'forest', 'sunset', 'sunrise']):
            detection["recommended_preset"] = "landscape"
        elif any(word in prompt_lower for word in ['product', 'item', 'object', 'merchandise', 'bottle', 'package']):
            detection["recommended_preset"] = "product_photo"
        elif any(word in prompt_lower for word in ['infographic', 'diagram', 'chart', 'icon', 'vector', 'flat']):
            detection["recommended_preset"] = "infographic_preset"

        if detection["has_person"] and detection["has_hands"] and detection["has_action"]:
            detection["warnings"].append("Complex scene with person + hands + action may require multiple attempts")
        if len(detection["detected_actions"]) > 1:
            detection["warnings"].append("Multiple actions detected - simpler prompts often yield better results")

        return detection

    def enhance_prompt_for_quality(self, prompt: str, style: str = "realistic",
                                   content_preset: Optional[str] = None,
                                   auto_enhance: bool = True,
                                   enhance_anatomy: bool = True,
                                   enhance_faces: bool = True,
                                   enhance_hands: bool = True) -> Tuple[str, str, Dict[str, Any]]:
        logger.info(f"--- DEBUG: ORIGINAL PROMPT TO ENHANCE: '{prompt}' (auto_enhance={auto_enhance}) ---")
        detection = self.detect_content_type(prompt)

        preset_name = content_preset or detection["recommended_preset"]
        preset = self.content_presets.get(preset_name, self.content_presets["general"])
        style_config = self.style_configs.get(style, self.style_configs["realistic"])

        enhancements = []
        negative_parts = []

        enhancements.append(style_config.get("positive_suffix", ""))
        enhancements.append(preset.get("positive_suffix", ""))

        negative_parts.append(self.base_negative)
        negative_parts.append(style_config.get("negative_prompt", ""))
        negative_parts.append(preset.get("negative_prompt", ""))

        if auto_enhance:
            if detection["has_person"] and enhance_anatomy:
                enhancements.append("correct human proportions, realistic anatomy, proper body structure")
                negative_parts.append(self.anatomy_negative)

            if detection["has_face"] and enhance_faces:
                enhancements.append("detailed facial features, symmetrical face, natural expression")
                negative_parts.append(self.face_negative)

            if detection["has_hands"] and enhance_hands:
                enhancements.append("correctly drawn hands, proper finger count, natural hand position")
                negative_parts.append(self.hands_negative)

            action_enhancements = {
                'building': ['construction scene', 'realistic work pose', 'focused activity'],
                'working': ['realistic work environment', 'logical positioning', 'professional setting'],
                'cooking': ['kitchen scene', 'realistic cooking pose', 'culinary activity'],
                'driving': ['hands on steering wheel', 'seated in vehicle', 'vehicle interior'],
                'typing': ['fingers on keyboard', 'seated at desk', 'office setting'],
                'reading': ['natural reading pose', 'focused attention'],
                'sports': ['athletic pose', 'dynamic movement', 'active motion'],
                'gardening': ['outdoor setting', 'natural environment', 'gardening activity']
            }

            is_single_subject = detection.get("subject_count_info", {}).get("is_single_subject", True)

            for action in detection["detected_actions"]:
                if action in action_enhancements:
                    enhancements.extend(action_enhancements[action])
                    negative_parts.append(f"floating objects, illogical {action}")

            if detection["has_spatial"]:
                enhancements.append("correct spatial relationships, logical positioning, proper depth, consistent perspective")
                negative_parts.append("wrong perspective, floating objects, incorrect scale, impossible physics")

            if detection["has_interaction"] and not is_single_subject:
                enhancements.append("realistic interaction, natural positioning")
                negative_parts.append("awkward poses, impossible poses")

            enhancements.append("coherent scene, logical composition, consistent lighting, unified style")
            negative_parts.append("inconsistent elements, mixed styles, impossible scene, conflicting perspectives")

        unique_enhancements = []
        seen = set()
        for e in enhancements:
            e_clean = e.strip()
            if e_clean and e_clean.lower() not in seen:
                seen.add(e_clean.lower())
                unique_enhancements.append(e_clean)

        enhanced_prompt = f"{prompt}, {', '.join(unique_enhancements)}"
        logger.info(f"--- DEBUG: RESULTING ENHANCED PROMPT: '{enhanced_prompt}' ---")

        unique_negatives = []
        seen_neg = set()
        for n in negative_parts:
            for part in n.split(', '):
                part_clean = part.strip()
                if part_clean and part_clean.lower() not in seen_neg:
                    seen_neg.add(part_clean.lower())
                    unique_negatives.append(part_clean)

        negative_prompt = ", ".join(unique_negatives)

        detection["preset_used"] = preset_name
        detection["style_used"] = style
        detection["enhancements_applied"] = unique_enhancements

        return enhanced_prompt, negative_prompt, detection

    def _enhance_prompt(self, prompt: str, style: str) -> Tuple[str, str]:
        if any(keyword in prompt.lower() for keyword in ['elements:', 'style keywords:', 'negative prompt:']):
            style_config = self.style_configs.get(style, self.style_configs["realistic"])
            return prompt, style_config['negative_prompt']

        enhanced_prompt, negative_prompt, _ = self.enhance_prompt_for_quality(
            prompt=prompt,
            style=style,
            auto_enhance=True
        )

        return enhanced_prompt, negative_prompt

    def _optimize_prompt_for_tokens(self, prompt: str, max_tokens: int = 75) -> str:
        words = prompt.split()
        if len(words) <= max_tokens:
            return prompt
        
        if any(keyword in prompt.lower() for keyword in ['elements:', 'style keywords:', 'negative prompt:']):
            main_desc = prompt.split('\n')[0].strip()
            return main_desc
        
        words = prompt.split()
        if len(words) > max_tokens:
            important_keywords = ['high quality', 'detailed', 'professional', 'clean', 'minimal']
            truncated_words = words[:max_tokens-3]
            
            for keyword in important_keywords:
                if keyword not in ' '.join(truncated_words) and len(truncated_words) < max_tokens:
                    truncated_words.append(keyword)
            
            return ' '.join(truncated_words)
        
        return prompt

    def get_prompt_templates(self) -> Dict[str, Dict[str, Any]]:
        return {
            "infographic": {
                "template": """{subject}, {style}, {color_palette}, {background}, {elements}, {mood}

Elements: {element_list}

Style Keywords: {style_keywords}

Negative Prompt: {negative_prompt}""",
                "example": {
                    "subject": "flat vector illustration, infographic style",
                    "style": "clean geometric forms, minimal shadows",
                    "color_palette": "muted palette of blues and grays with accent red",
                    "background": "legal courtroom background with courthouse columns",
                    "elements": "scales of justice, legal documents, gavel, judge's bench silhouette, professional briefcase",
                    "mood": "serious tone",
                    "element_list": "gavel, legal documents with seal, scale of justice, professional desk, law books",
                    "style_keywords": "legal services, professional, corporate law, business consultation, justice, legal practice",
                    "negative_prompt": "no photorealism, no people faces, no over-saturation, no glitter or cartoon color, no watermarks"
                }
            },
            "realistic": {
                "template": "{subject}, {quality}, {lighting}, {composition}, {mood}",
                "example": {
                    "subject": "A majestic mountain landscape at sunset",
                    "quality": "photorealistic, high quality, detailed, sharp focus",
                    "lighting": "golden hour lighting, dramatic clouds",
                    "composition": "balanced composition, professional photography",
                    "mood": "peaceful mood, serene atmosphere"
                }
            },
            "technical": {
                "template": "{subject}, {style}, {details}, {composition}",
                "example": {
                    "subject": "technical diagram of a system",
                    "style": "clean lines, precise details, professional diagram",
                    "details": "clear labels, minimal style, technical illustration",
                    "composition": "clear composition, balanced layout"
                }
            }
        }

    def get_quality_presets(self) -> Dict[str, Dict[str, Any]]:
        return {
            "fast": {
                "num_inference_steps": 15,
                "guidance_scale": 7.0,
                "description": "Quick generation, good for testing"
            },
            "standard": {
                "num_inference_steps": 20,
                "guidance_scale": 7.5,
                "description": "Balanced quality and speed"
            },
            "high": {
                "num_inference_steps": 30,
                "guidance_scale": 8.0,
                "description": "High quality, slower generation"
            },
            "professional": {
                "num_inference_steps": 25,
                "guidance_scale": 7.5,
                "description": "Professional quality for final output"
            }
        }

    def _notify_vision_pipeline(self, action: str):
        """Best-effort notification to vision pipeline. Fire and forget."""
        try:
            import requests as req
            req.post("http://localhost:8201/gpu/contention",
                     json={"source": "image_gen", "action": action}, timeout=1)
        except Exception:
            pass

    def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        start_time = time.time()

        result = ImageGenerationResult(
            success=False,
            prompt_used=request.prompt,
            negative_prompt_used=request.negative_prompt,
            image_size=(request.width, request.height)
        )

        if not self.service_available:
            result.error = "Image generation service not available - missing dependencies"
            return result

        with self._generation_lock:
            self._notify_vision_pipeline("start")
            try:
                model_id = self.available_models.get(request.model, self.default_model)
                logger.info(f"Using model: {request.model} -> {model_id}")

                is_sdxl = 'xl' in model_id.lower() or 'sdxl' in model_id.lower()

                if is_sdxl and request.guidance_scale > 9.0:
                    logger.warning(f"Guidance scale {request.guidance_scale} is too high for SDXL (causes black images). Auto-correcting to 7.5")
                    request.guidance_scale = 7.5
                elif is_sdxl and request.guidance_scale < 4.0:
                    logger.warning(f"Guidance scale {request.guidance_scale} is too low for SDXL. Auto-correcting to 6.0")
                    request.guidance_scale = 6.0
                elif not is_sdxl and request.guidance_scale > 20.0:
                    logger.warning(f"Guidance scale {request.guidance_scale} is extremely high. Capping at 15.0")
                    request.guidance_scale = 15.0

                if not self._load_pipeline(model_id):
                    result.error = f"Failed to load model {request.model} ({model_id})"
                    return result

                if request.auto_enhance:
                    enhanced_prompt, style_negative, detection = self.enhance_prompt_for_quality(
                        prompt=request.prompt,
                        style=request.style,
                        content_preset=request.content_preset,
                        auto_enhance=True,
                        enhance_anatomy=request.enhance_anatomy,
                        enhance_faces=request.enhance_faces,
                        enhance_hands=request.enhance_hands
                    )
                    logger.info(f"Content detection: {detection.get('recommended_preset')}, enhancements: {len(detection.get('enhancements_applied', []))}")
                else:
                    enhanced_prompt, style_negative = self._enhance_prompt(request.prompt, request.style)
                    detection = {}

                enhanced_prompt = self._optimize_prompt_for_tokens(enhanced_prompt)

                combined_negative = request.negative_prompt
                if style_negative:
                    combined_negative = f"{combined_negative}, {style_negative}" if combined_negative else style_negative

                generator = None
                if request.seed is not None:
                    generator = torch.Generator(device=self._device).manual_seed(request.seed)
                    result.seed_used = request.seed
                else:
                    seed = torch.randint(0, 2**32, (1,)).item()
                    generator = torch.Generator(device=self._device).manual_seed(seed)
                    result.seed_used = seed

                logger.info(f"--- DEBUG: FINAL PROMPT SENT TO MODEL: '{enhanced_prompt}' ---")
                logger.info(f"--- DEBUG: FINAL NEGATIVE PROMPT: '{combined_negative}' ---")
                logger.info(f"Generating image: {enhanced_prompt[:100]}...")

                if self._device == "cuda":
                    try:
                        with torch.autocast("cuda"):
                            output = self._pipeline(
                                prompt=enhanced_prompt,
                                negative_prompt=combined_negative,
                                width=request.width,
                                height=request.height,
                                num_inference_steps=request.num_inference_steps,
                                guidance_scale=request.guidance_scale,
                                generator=generator
                            )
                    except (AssertionError, RuntimeError) as compile_err:
                        is_compile_failure = (
                            (isinstance(compile_err, AssertionError) and not str(compile_err))
                            or any(kw in str(compile_err).lower() for kw in
                                   ('triton', 'dynamo', 'inductor', 'cuda graph', 'torch.compile'))
                        )
                        has_compiled_modules = (
                            self._compile_unet_orig is not None or self._compile_vae_orig is not None
                        )
                        if is_compile_failure and has_compiled_modules and not self._compile_failed:
                            logger.warning(
                                f"torch.compile first-pass failure "
                                f"({type(compile_err).__name__}: {compile_err or 'no message'}) "
                                f"— stripping compiled wrappers and retrying in eager mode"
                            )
                            if self._compile_unet_orig is not None:
                                self._pipeline.unet = self._compile_unet_orig
                            if self._compile_vae_orig is not None:
                                self._pipeline.vae = self._compile_vae_orig
                            self._compile_failed = True
                            with torch.autocast("cuda"):
                                output = self._pipeline(
                                    prompt=enhanced_prompt,
                                    negative_prompt=combined_negative,
                                    width=request.width,
                                    height=request.height,
                                    num_inference_steps=request.num_inference_steps,
                                    guidance_scale=request.guidance_scale,
                                    generator=generator
                                )
                        else:
                            raise
                else:
                    output = self._pipeline(
                        prompt=enhanced_prompt,
                        negative_prompt=combined_negative,
                        width=request.width,
                        height=request.height,
                        num_inference_steps=request.num_inference_steps,
                        guidance_scale=request.guidance_scale,
                        generator=generator
                    )

                # Some diffusers versions silently replace images with black
                # when internal NSFW detection triggers, even with safety_checker=None.
                # Check and override this behavior.
                if hasattr(output, 'nsfw_content_detected') and output.nsfw_content_detected:
                    if any(output.nsfw_content_detected):
                        logger.warning("Diffusers NSFW flag detected but safety_checker is disabled — ignoring flag")

                image = output.images[0]
                if image is None:
                    result.error = "Pipeline returned no image"
                    result.generation_time = time.time() - start_time
                    return result

                image_id = str(uuid.uuid4())
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"generated_{timestamp}_{image_id}.png"
                image_path = self.cache_dir / filename

                image.save(image_path, "PNG")

                face_restoration_metadata = None
                if request.restore_faces:
                    try:
                        face_service = get_face_restoration_service()
                        service_available = face_service.service_available
                    except Exception as e:
                        logger.warning(f"Could not check face restoration availability: {e}")
                        service_available = False

                    if service_available:
                        should_restore = detection.get("has_person") or detection.get("has_face") if detection else False

                        if should_restore:
                            logger.info("Applying GFPGAN face restoration...")
                            try:
                                success, restored_pil, restore_meta = face_service.restore_face_from_pil(
                                    image=image,
                                    weight=request.face_restoration_weight
                                )

                                if success and restored_pil:
                                    image = restored_pil
                                    image.save(image_path, "PNG")
                                    face_restoration_metadata = restore_meta
                                    logger.info(f"Face restoration applied: {restore_meta.get('faces_detected', 0)} faces enhanced")
                                else:
                                    logger.warning(f"Face restoration failed: {restore_meta.get('error', 'Unknown error') if restore_meta else 'No metadata'}")
                            except Exception as e:
                                logger.error(f"Face restoration error: {e}")
                        else:
                            logger.debug("Skipping face restoration - no faces detected in prompt")
                    else:
                        logger.debug("Face restoration requested but service not available")

                result.success = True
                result.image_path = str(image_path)
                result.prompt_used = enhanced_prompt
                result.negative_prompt_used = combined_negative
                result.model_used = self._current_model
                result.generation_time = time.time() - start_time
                result.metadata = {
                    "steps": request.num_inference_steps,
                    "guidance_scale": request.guidance_scale,
                    "style": request.style,
                    "device": self._device,
                    "auto_enhance": request.auto_enhance,
                    "content_preset": detection.get("preset_used") if detection else None,
                    "content_detection": {
                        "has_person": detection.get("has_person"),
                        "has_face": detection.get("has_face"),
                        "has_hands": detection.get("has_hands"),
                        "has_action": detection.get("has_action"),
                        "detected_actions": detection.get("detected_actions", [])
                    } if detection else None,
                    "face_restoration": face_restoration_metadata
                }

                logger.info(f"Image generated successfully in {result.generation_time:.2f}s: {image_path}")

            except Exception as e:
                logger.error(f"Image generation failed: {type(e).__name__}: {e}", exc_info=True)
                error_msg = str(e) or f"{type(e).__name__} (no message)"
                result.error = f"Generation failed: {error_msg}"
                result.generation_time = time.time() - start_time
            finally:
                self._notify_vision_pipeline("stop")

        return result

    def generate_image_from_image(
        self, prompt: str, init_image, strength: float = 0.20,
        negative_prompt: str = "", width: int = 512, height: int = 512,
        num_inference_steps: int = 20, guidance_scale: float = 7.5,
        seed: int = None, model: str = "sd-1.5"
    ) -> ImageGenerationResult:
        """Generate an image using img2img — takes an existing PIL Image and
        produces a variation guided by the prompt and strength parameter.

        Args:
            prompt: Text prompt for the output image.
            init_image: PIL.Image input frame.
            strength: How much to change (0.0=identical, 1.0=ignore input).
            Other args mirror generate_image().

        Returns:
            ImageGenerationResult with the new image path.
        """
        result = ImageGenerationResult(success=False)
        start_time = time.time()

        if not self.service_available:
            result.error = "Image generation service not available"
            return result

        with self._generation_lock:
            self._notify_vision_pipeline("start")
            try:
                model_id = self.available_models.get(model, model)
                is_sdxl = 'xl' in model_id.lower() or 'sdxl' in model_id.lower()

                # Ensure the base txt2img pipeline is loaded (downloads model if needed)
                if not self._load_pipeline(model_id):
                    result.error = f"Failed to load model {model} ({model_id})"
                    return result

                # Load img2img pipeline from same weights (shares VAE/UNet/text encoder)
                if self._img2img_pipeline is None or self._current_model != model_id:
                    model_path = self._get_model_path(model_id)
                    logger.info(f"Loading img2img pipeline from {model_path}")
                    self._img2img_pipeline = StableDiffusionImg2ImgPipeline(
                        vae=self._pipeline.vae,
                        text_encoder=self._pipeline.text_encoder,
                        tokenizer=self._pipeline.tokenizer,
                        unet=self._pipeline.unet,
                        scheduler=self._pipeline.scheduler,
                        safety_checker=None,
                        feature_extractor=None,
                        requires_safety_checker=False,
                    )
                    logger.info("img2img pipeline ready (shared weights)")

                # Resize init_image to target dimensions
                if init_image.size != (width, height):
                    init_image = init_image.resize((width, height), Image.LANCZOS)

                # Convert to RGB if needed
                if init_image.mode != "RGB":
                    init_image = init_image.convert("RGB")

                generator = None
                if seed is not None:
                    generator = torch.Generator(device=self._device).manual_seed(seed)
                    result.seed_used = seed
                else:
                    seed = torch.randint(0, 2**32, (1,)).item()
                    generator = torch.Generator(device=self._device).manual_seed(seed)
                    result.seed_used = seed

                combined_negative = negative_prompt or "blurry, low quality, distorted"

                logger.info(f"img2img: strength={strength}, steps={num_inference_steps}, prompt={prompt[:80]}...")

                if self._device == "cuda":
                    with torch.autocast("cuda"):
                        output = self._img2img_pipeline(
                            prompt=prompt,
                            image=init_image,
                            strength=strength,
                            negative_prompt=combined_negative,
                            num_inference_steps=num_inference_steps,
                            guidance_scale=guidance_scale,
                            generator=generator,
                        )
                else:
                    output = self._img2img_pipeline(
                        prompt=prompt,
                        image=init_image,
                        strength=strength,
                        negative_prompt=combined_negative,
                        num_inference_steps=num_inference_steps,
                        guidance_scale=guidance_scale,
                        generator=generator,
                    )

                image = output.images[0]
                if image is None:
                    result.error = "img2img pipeline returned no image"
                    result.generation_time = time.time() - start_time
                    return result

                image_id = str(uuid.uuid4())
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"img2img_{timestamp}_{image_id}.png"
                image_path = self.cache_dir / filename
                image.save(image_path, "PNG")

                result.success = True
                result.image_path = str(image_path)
                result.prompt_used = prompt
                result.negative_prompt_used = combined_negative
                result.model_used = self._current_model
                result.generation_time = time.time() - start_time

                logger.info(f"img2img generated in {result.generation_time:.2f}s: {image_path}")

            except Exception as e:
                logger.error(f"img2img failed: {type(e).__name__}: {e}", exc_info=True)
                error_msg = str(e) or f"{type(e).__name__} (no message)"
                result.error = f"img2img failed: {error_msg}"
                result.generation_time = time.time() - start_time
            finally:
                self._notify_vision_pipeline("stop")

        return result

    def get_available_models(self) -> Dict[str, Any]:
        models = {}

        for model_key, model_id in self.available_models.items():
            models[model_key] = {
                "id": model_id,
                "name": model_key,
                "downloaded": self._is_model_downloaded(model_id),
                "current": model_id == self._current_model,
                "size_estimate": "4-7GB" if "xl" not in model_id.lower() else "12-15GB"
            }

        return models

    def get_service_status(self) -> Dict[str, Any]:
        optimizations = {}
        
        if self._pipeline:
            optimizations = {
                "attention_slicing": hasattr(self._pipeline, "enable_attention_slicing"),
                "xformers_available": hasattr(self._pipeline, "enable_xformers_memory_efficient_attention"),
                "vae_slicing": hasattr(self._pipeline, "enable_vae_slicing"),
                "vae_tiling": hasattr(self._pipeline, "enable_vae_tiling"),
                "torch_compile_available": hasattr(torch, 'compile'),
                "cpu_offloading_disabled": True
            }
        
        return {
            "service_available": self.service_available,
            "device": self._device,
            "cuda_available": torch.cuda.is_available() if diffusion_available else False,
            "current_model": self._current_model,
            "models_dir": str(self.models_dir),
            "cache_dir": str(self.cache_dir),
            "available_models": self.get_available_models(),
            "available_styles": list(self.style_configs.keys()),
            "optimizations": optimizations,
            "pytorch_version": torch.__version__ if diffusion_available else "N/A",
            "prompt_templates": self.get_prompt_templates(),
            "quality_presets": self.get_quality_presets()
        }

    def clear_cache(self) -> Dict[str, Any]:
        try:
            import shutil
            if self.cache_dir.exists():
                shutil.rmtree(self.cache_dir)
                self.cache_dir.mkdir(parents=True, exist_ok=True)

            return {"success": True, "message": "Cache cleared successfully"}

        except Exception as e:
            return {"success": False, "error": str(e)}


_generator_instance = None

def get_image_generator() -> OfflineImageGenerator:
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = OfflineImageGenerator()
    return _generator_instance


def generate_image(prompt: str, style: str = "realistic", width: int = 512, height: int = 512,
                  steps: int = 20, guidance: float = 7.5, seed: Optional[int] = None) -> ImageGenerationResult:
    request = ImageGenerationRequest(
        prompt=prompt,
        width=width,
        height=height,
        num_inference_steps=steps,
        guidance_scale=guidance,
        style=style,
        seed=seed
    )

    generator = get_image_generator()
    return generator.generate_image(request)


def get_generator_status() -> Dict[str, Any]:
    generator = get_image_generator()
    return generator.get_service_status()
