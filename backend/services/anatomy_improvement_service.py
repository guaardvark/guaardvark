import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
import numpy as np

logger = logging.getLogger(__name__)

try:
    from PIL import Image
    import cv2
    import torch
    from diffusers import (
        StableDiffusionControlNetPipeline,
        ControlNetModel,
        UniPCMultistepScheduler,
    )
    from controlnet_aux import OpenposeDetector, MidasDetector
    import mediapipe as mp

    controlnet_available = True
    logger.info("ControlNet dependencies loaded successfully")
except ImportError as e:
    controlnet_available = False
    logger.warning(f"ControlNet dependencies not available: {e}")

try:
    from backend.config import CACHE_DIR

    config_available = True
except ImportError:
    config_available = False
    CACHE_DIR = "/tmp/guaardvark_cache"


class AnatomyImprovementService:

    def __init__(self):
        project_root = Path(__file__).parent.parent.parent
        self.models_dir = project_root / "data" / "models" / "controlnet"
        self.models_dir.mkdir(parents=True, exist_ok=True)

        self._device = "cuda" if torch.cuda.is_available() else "cpu"

        self._controlnet_pose = None
        self._controlnet_depth = None
        self._openpose_detector = None
        self._depth_detector = None
        self._pipeline = None

        self.service_available = controlnet_available

        logger.info(
            f"AnatomyImprovementService initialized - Device: {self._device}, Available: {self.service_available}"
        )

    def _load_openpose_detector(self) -> bool:
        if not self.service_available:
            return False

        if self._openpose_detector is not None:
            return True

        try:
            logger.info("Loading OpenPose detector...")
            self._openpose_detector = OpenposeDetector.from_pretrained(
                "lllyasviel/ControlNet"
            )
            logger.info("OpenPose detector loaded successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to load OpenPose detector: {e}")
            return False

    def _load_depth_detector(self) -> bool:
        if not self.service_available:
            return False

        if self._depth_detector is not None:
            return True

        try:
            logger.info("Loading depth detector...")
            self._depth_detector = MidasDetector.from_pretrained(
                "lllyasviel/ControlNet"
            )
            logger.info("Depth detector loaded successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to load depth detector: {e}")
            return False

    def _load_controlnet_pipeline(self, controlnet_type: str = "pose") -> bool:
        if not self.service_available:
            return False

        try:
            logger.info(f"Loading ControlNet pipeline ({controlnet_type})...")

            if controlnet_type == "pose":
                controlnet = ControlNetModel.from_pretrained(
                    "lllyasviel/control_v11p_sd15_openpose",
                    torch_dtype=(
                        torch.float16 if self._device == "cuda" else torch.float32
                    ),
                )
                self._controlnet_pose = controlnet
            elif controlnet_type == "depth":
                controlnet = ControlNetModel.from_pretrained(
                    "lllyasviel/control_v11f1p_sd15_depth",
                    torch_dtype=(
                        torch.float16 if self._device == "cuda" else torch.float32
                    ),
                )
                self._controlnet_depth = controlnet
            else:
                raise ValueError(f"Unknown controlnet_type: {controlnet_type}")

            self._pipeline = StableDiffusionControlNetPipeline.from_pretrained(
                "SG161222/Realistic_Vision_V5.1_noVAE",
                controlnet=controlnet,
                torch_dtype=torch.float16 if self._device == "cuda" else torch.float32,
                safety_checker=None,
            )

            self._pipeline.scheduler = UniPCMultistepScheduler.from_config(
                self._pipeline.scheduler.config
            )
            self._pipeline = self._pipeline.to(self._device)

            if hasattr(self._pipeline, "enable_attention_slicing"):
                self._pipeline.enable_attention_slicing()

            logger.info(f"ControlNet pipeline ({controlnet_type}) loaded successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to load ControlNet pipeline: {e}")
            return False

    def extract_pose(
        self, image: Image.Image
    ) -> Tuple[bool, Optional[Image.Image], Dict[str, Any]]:
        if not self.service_available:
            return False, None, {"error": "ControlNet service not available"}

        try:
            if not self._load_openpose_detector():
                return False, None, {"error": "Failed to load OpenPose detector"}

            logger.info("Extracting pose from image...")

            pose_image = self._openpose_detector(image)

            metadata = {
                "detector": "openpose",
                "input_size": image.size,
                "output_size": pose_image.size,
            }

            logger.info("Pose extraction complete")
            return True, pose_image, metadata

        except Exception as e:
            logger.error(f"Pose extraction failed: {e}")
            return False, None, {"error": str(e)}

    def extract_depth(
        self, image: Image.Image
    ) -> Tuple[bool, Optional[Image.Image], Dict[str, Any]]:
        if not self.service_available:
            return False, None, {"error": "ControlNet service not available"}

        try:
            if not self._load_depth_detector():
                return False, None, {"error": "Failed to load depth detector"}

            logger.info("Extracting depth map from image...")

            depth_image = self._depth_detector(image)

            metadata = {
                "detector": "midas",
                "input_size": image.size,
                "output_size": depth_image.size,
            }

            logger.info("Depth extraction complete")
            return True, depth_image, metadata

        except Exception as e:
            logger.error(f"Depth extraction failed: {e}")
            return False, None, {"error": str(e)}

    def generate_with_pose_control(
        self,
        prompt: str,
        pose_image: Image.Image,
        negative_prompt: str = "",
        num_inference_steps: int = 30,
        guidance_scale: float = 8.0,
        controlnet_conditioning_scale: float = 1.0,
        width: int = 512,
        height: int = 768,
        seed: Optional[int] = None,
    ) -> Tuple[bool, Optional[Image.Image], Dict[str, Any]]:
        if not self.service_available:
            return False, None, {"error": "ControlNet service not available"}

        try:
            if not self._load_controlnet_pipeline("pose"):
                return False, None, {"error": "Failed to load ControlNet pipeline"}

            if not self._is_pose_skeleton(pose_image):
                logger.info("Input is not a pose skeleton, extracting pose...")
                success, pose_image, _ = self.extract_pose(pose_image)
                if not success:
                    return (
                        False,
                        None,
                        {"error": "Failed to extract pose from reference image"},
                    )

            generator = None
            if seed is not None:
                generator = torch.Generator(device=self._device).manual_seed(seed)

            logger.info(f"Generating image with pose control: {prompt[:100]}...")

            output = self._pipeline(
                prompt=prompt,
                negative_prompt=negative_prompt,
                image=pose_image,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                controlnet_conditioning_scale=controlnet_conditioning_scale,
                width=width,
                height=height,
                generator=generator,
            )

            generated_image = output.images[0]

            metadata = {
                "controlnet_type": "pose",
                "conditioning_scale": controlnet_conditioning_scale,
                "steps": num_inference_steps,
                "guidance_scale": guidance_scale,
                "size": (width, height),
                "seed": seed,
            }

            logger.info("Pose-controlled generation complete")
            return True, generated_image, metadata

        except Exception as e:
            logger.error(f"Pose-controlled generation failed: {e}")
            return False, None, {"error": str(e)}

    def _is_pose_skeleton(self, image: Image.Image) -> bool:
        img_array = np.array(image)

        grayscale = (
            cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
            if len(img_array.shape) == 3
            else img_array
        )
        unique_colors = len(np.unique(grayscale))

        return unique_colors < 50

    def get_anatomy_negative_prompts(self) -> Dict[str, str]:
        return {
            "body_base": "deformed body, distorted anatomy, missing body parts, extra body parts, asymmetrical body, disproportionate limbs, twisted torso, elongated neck, short neck, no neck",
            "limbs": "extra arms, missing arms, extra legs, missing legs, fused limbs, disconnected limbs, floating limbs, wrong number of fingers, extra fingers, missing fingers, fused fingers, webbed fingers, claw hands, malformed hands, backwards hands, wrong hand orientation",
            "joints": "broken joints, dislocated joints, impossible joint angles, reverse joints, double joints, missing joints, fused joints",
            "face_detailed": "asymmetrical face, lopsided face, distorted facial features, missing eyes, extra eyes, eyes looking in different directions, cross-eyed, uneven eyes, floating eyes, nose too big, nose too small, missing nose, extra nose, mouth too wide, mouth too small, missing mouth, teeth showing through closed lips, wrong teeth",
            "posture": "impossible pose, broken spine, twisted spine, unnatural stance, weight distribution wrong, floating above ground, sinking into ground, leaning at impossible angle",
            "proportions": "head too big, head too small, torso too long, torso too short, arms too long, arms too short, legs too long, legs too short, hands too big, hands too small, feet too big, feet too small",
            "skin": "patchy skin, missing skin, extra skin, skin tears, skin folds in wrong places, texture inconsistency",
        }

    def get_enhanced_anatomy_prompt(
        self, base_prompt: str, focus_areas: List[str] = None
    ) -> Tuple[str, str]:
        anatomy_positives = {
            "general": "correct human anatomy, proper body proportions, natural pose, realistic body structure",
            "hands": "anatomically correct hands, five fingers per hand, properly formed fingers, natural hand position, realistic hand structure",
            "face": "symmetrical face, properly aligned facial features, natural facial proportions, realistic eyes, correct eye placement",
            "posture": "natural body posture, balanced stance, realistic weight distribution, proper skeletal alignment",
            "limbs": "correctly proportioned limbs, proper joint placement, natural limb positioning, realistic arm and leg length",
        }

        enhancements = [anatomy_positives["general"]]
        if focus_areas:
            for area in focus_areas:
                if area in anatomy_positives:
                    enhancements.append(anatomy_positives[area])

        enhanced_positive = f"{base_prompt}, {', '.join(enhancements)}"

        negative_prompts = self.get_anatomy_negative_prompts()
        negative_parts = [
            negative_prompts["body_base"],
            negative_prompts["proportions"],
        ]

        if focus_areas:
            for area in focus_areas:
                if area == "hands":
                    negative_parts.append(negative_prompts["limbs"])
                elif area == "face":
                    negative_parts.append(negative_prompts["face_detailed"])
                elif area == "posture":
                    negative_parts.append(negative_prompts["posture"])
                    negative_parts.append(negative_prompts["joints"])
        else:
            negative_parts.extend(
                [
                    negative_prompts["limbs"],
                    negative_prompts["face_detailed"],
                    negative_prompts["joints"],
                    negative_prompts["posture"],
                ]
            )

        enhanced_negative = ", ".join(negative_parts)

        return enhanced_positive, enhanced_negative

    def get_service_status(self) -> Dict[str, Any]:
        return {
            "service_available": self.service_available,
            "device": self._device,
            "cuda_available": (
                torch.cuda.is_available() if controlnet_available else False
            ),
            "openpose_loaded": self._openpose_detector is not None,
            "depth_loaded": self._depth_detector is not None,
            "controlnet_pose_loaded": self._controlnet_pose is not None,
            "controlnet_depth_loaded": self._controlnet_depth is not None,
            "models_dir": str(self.models_dir),
        }


_anatomy_service = None


def get_anatomy_service() -> AnatomyImprovementService:
    global _anatomy_service
    if _anatomy_service is None:
        _anatomy_service = AnatomyImprovementService()
    return _anatomy_service
