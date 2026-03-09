# backend/services/vision_chat_service.py
# Vision Chat Service - Interactive Vision Capabilities
# Handles image analysis and generation in chat conversations

import logging
import os
import base64
import tempfile
from typing import Optional, Dict, Any, List, Union
from pathlib import Path
from dataclasses import dataclass
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

# Import dependencies with fallback handling
try:
    from backend.services.image_content_service import image_extractor
    image_analysis_available = True
except ImportError:
    logger.warning("Image content service not available")
    image_analysis_available = False

try:
    from backend.config import OLLAMA_BASE_URL, LLM_REQUEST_TIMEOUT
    import requests
    config_available = True
except ImportError as e:
    logger.error(f"Failed to import config dependencies: {e}")
    config_available = False
    OLLAMA_BASE_URL = "http://localhost:11434"
    LLM_REQUEST_TIMEOUT = 120

@dataclass
class ImageAnalysisResult:
    """Result of image analysis operation"""
    success: bool
    description: str = ""
    extracted_text: str = ""
    objects_detected: List[str] = None
    confidence: float = 0.0
    model_used: str = ""
    processing_time: float = 0.0
    error: Optional[str] = None

@dataclass
class ImageGenerationResult:
    """Result of image generation operation"""
    success: bool
    image_path: Optional[str] = None
    image_data: Optional[bytes] = None
    prompt_used: str = ""
    model_used: str = ""
    generation_time: float = 0.0
    image_size: tuple = (512, 512)
    error: Optional[str] = None

class VisionChatService:
    """Service for handling vision capabilities in chat conversations."""
    
    def __init__(self):
        # Vision models now auto-detected via chat_utils (no hardcoded fallback list)
        self.fallback_vision_models = []  # Populated dynamically by _get_available_models()
        self.image_gen_models = ["sdxl", "sdxl:latest", "stable-diffusion", "dall-e"]
        self.supported_image_formats = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
        self.max_image_size_mb = 20  # Larger limit for chat images
        from backend.config import CACHE_DIR
        self.temp_image_dir = os.path.join(CACHE_DIR, "chat_images")
        
        # Ensure temp directory exists
        os.makedirs(self.temp_image_dir, exist_ok=True)
        
        self.service_available = config_available
        
    def _get_available_models(self, model_type: str = "vision") -> List[str]:
        """Get available models of specified type from Ollama, prioritizing current model."""
        if not self.service_available:
            return []
            
        available_models = []
        
        # For vision models, first check if current text model supports vision
        if model_type == "vision":
            try:
                from backend.utils.chat_utils import is_vision_model
                from flask import current_app
                from llama_index.core import Settings

                # Get current text model
                llm = Settings.llm or current_app.config.get("LLAMA_INDEX_LLM")
                current_model = getattr(llm, "model", None) if llm else None

                if current_model and is_vision_model(current_model):
                    # Check if current model is loaded and available
                    try:
                        resp = requests.post(
                            f"{OLLAMA_BASE_URL}/api/show",
                            json={"name": current_model},
                            timeout=5
                        )
                        if resp.ok:
                            logger.info(f"Using current vision-capable model: {current_model}")
                            return [current_model]
                    except Exception as e:
                        logger.debug(f"Current model check failed: {e}")

            except ImportError as e:
                logger.warning(f"Could not import vision detection utilities: {e}")
            except Exception as e:
                logger.debug(f"Error checking current model for vision: {e}")

        # Fallback to checking available models in Ollama
        try:
            resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10)
            if not resp.ok:
                return []

            models_data = resp.json().get('models', [])

            if model_type == "vision":
                # Use pattern-based detection for ALL available models
                from backend.utils.chat_utils import is_vision_model as _is_vision
                for model_data in models_data:
                    if isinstance(model_data, dict):
                        name = model_data.get("name", "")
                        if name and _is_vision(name):
                            available_models.append(name)
            else:
                target_models = self.image_gen_models
                for model_data in models_data:
                    if isinstance(model_data, dict):
                        model_name = model_data.get("name", "").lower()
                        for target_model in target_models:
                            if target_model.lower() in model_name:
                                available_models.append(model_data.get("name"))
                                break

            return available_models
            
        except Exception as e:
            logger.warning(f"Failed to get available {model_type} models: {e}")
            return []
    
    def _save_chat_image(self, image_data: bytes, extension: str = ".png") -> str:
        """Save image data to temporary location for chat processing."""
        try:
            # Generate unique filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = str(uuid.uuid4())[:8]
            filename = f"chat_image_{timestamp}_{unique_id}{extension}"
            
            image_path = os.path.join(self.temp_image_dir, filename)
            
            with open(image_path, 'wb') as f:
                f.write(image_data)
                
            logger.info(f"Saved chat image: {image_path}")
            return image_path
            
        except Exception as e:
            logger.error(f"Failed to save chat image: {e}")
            raise
    
    def _encode_image_for_ollama(self, image_path: str) -> Optional[str]:
        """Encode image to base64 for Ollama API."""
        try:
            with open(image_path, 'rb') as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                return encoded_string
        except Exception as e:
            logger.error(f"Failed to encode image {image_path}: {e}")
            return None
    
    def analyze_image_for_chat(self, image_data: bytes, prompt: str = "", analysis_type: str = "describe") -> ImageAnalysisResult:
        """
        Analyze an image in chat context with various analysis types.
        
        Args:
            image_data: Raw image bytes
            prompt: Custom analysis prompt
            analysis_type: Type of analysis - "describe", "ocr", "objects", "custom"
        """
        start_time = datetime.now()
        
        result = ImageAnalysisResult(
            success=False,
            objects_detected=[],
            error=None
        )
        
        if not self.service_available:
            result.error = "Vision service not available"
            return result
            
        try:
            # Save image temporarily
            image_path = self._save_chat_image(image_data)
            
            # Get available vision models
            vision_models = self._get_available_models("vision")
            if not vision_models:
                result.error = "No vision models available"
                return result
                
            vision_model = vision_models[0]  # Use first available
            
            # Create analysis prompt based on type
            if analysis_type == "describe":
                analysis_prompt = prompt or """Please describe this image in detail. Include:
1. What you see in the image
2. The overall scene or setting
3. Any notable objects, people, or activities
4. Colors, composition, and visual elements
5. Any text visible in the image

Provide a comprehensive but concise description suitable for a chat conversation."""
                
            elif analysis_type == "ocr":
                analysis_prompt = prompt or """Extract and transcribe all text visible in this image. Include:
1. Any written text, signs, labels, or captions
2. Text in any language or script
3. Handwritten text if legible
4. Numbers, symbols, or special characters

If no text is visible, respond with 'No text detected in image'."""
                
            elif analysis_type == "objects":
                analysis_prompt = prompt or """Identify and list all objects, people, and elements visible in this image. Provide:
1. A comprehensive list of all identifiable objects
2. People and their activities if present
3. Animals or living things
4. Architectural or environmental elements
5. Any brands, logos, or identifying marks

Format as a clear, organized list."""
                
            else:  # custom
                analysis_prompt = prompt or "Please analyze this image and provide relevant insights."
            
            # Perform vision analysis using Ollama API
            try:
                import ollama
                
                # Use direct Ollama client for better image handling
                client = ollama.Client(host=OLLAMA_BASE_URL)
                
                with open(image_path, 'rb') as image_file:
                    response = client.generate(
                        model=vision_model,
                        prompt=analysis_prompt,
                        images=[image_file.read()]
                    )
                
                analysis_result = response.get('response', '').strip()
                
                if analysis_result:
                    result.success = True
                    result.description = analysis_result
                    result.model_used = vision_model
                    result.confidence = 0.85  # Default confidence for successful analysis
                    
                    # Extract text if OCR type
                    if analysis_type == "ocr":
                        result.extracted_text = analysis_result
                        
                    logger.info(f"Successfully analyzed image using {vision_model}")
                else:
                    result.error = "No analysis result from vision model"
                    
            except ImportError:
                # Fallback to image content service if available
                if image_analysis_available:
                    logger.info("Using fallback image content service for analysis")
                    extraction_result = image_extractor.extract_text_from_image(image_path)
                    
                    if extraction_result.get('success'):
                        result.success = True
                        result.extracted_text = extraction_result.get('text_content', '')
                        result.description = f"Image processed with OCR. Text content: {result.extracted_text}" if result.extracted_text else "Image processed - no text content detected"
                        result.model_used = extraction_result.get('model_used', 'image_content_service')
                        result.confidence = extraction_result.get('confidence', 0.0)
                    else:
                        result.error = extraction_result.get('error', 'Image analysis failed')
                else:
                    result.error = "No vision analysis capabilities available"
            
            # Clean up temporary file
            try:
                os.unlink(image_path)
            except OSError:
                pass
                
        except Exception as e:
            logger.error(f"Error in image analysis: {e}")
            result.error = str(e)
            
        # Calculate processing time
        end_time = datetime.now()
        result.processing_time = (end_time - start_time).total_seconds()
        
        return result
    
    def generate_image_for_chat(self, prompt: str, style: str = "realistic", size: tuple = (512, 512)) -> ImageGenerationResult:
        """
        Generate an image based on text prompt for chat conversation.
        
        Args:
            prompt: Text description of desired image
            style: Generation style - "realistic", "artistic", "cartoon", "sketch"
            size: Desired image dimensions (width, height)
        """
        start_time = datetime.now()
        
        result = ImageGenerationResult(
            success=False,
            prompt_used=prompt,
            image_size=size,
            error=None
        )
        
        if not self.service_available:
            result.error = "Image generation service not available"
            return result
            
        try:
            # Get available image generation models
            gen_models = self._get_available_models("generation")
            if not gen_models:
                result.error = "No image generation models available"
                return result
                
            gen_model = gen_models[0]  # Use first available
            
            # Enhance prompt with style
            style_prompts = {
                "realistic": "photorealistic, high quality, detailed",
                "artistic": "artistic, beautiful, creative, masterpiece",
                "cartoon": "cartoon style, animated, colorful, fun",
                "sketch": "pencil sketch, hand-drawn, artistic lines"
            }
            
            enhanced_prompt = f"{prompt}, {style_prompts.get(style, 'high quality')}"
            
            # Generate image using Ollama API
            try:
                import ollama
                
                client = ollama.Client(host=OLLAMA_BASE_URL)
                
                # Note: Image generation API may vary by model
                # This is a placeholder for the actual implementation
                response = client.generate(
                    model=gen_model,
                    prompt=f"Generate an image: {enhanced_prompt}",
                    # Additional parameters for image generation
                    options={
                        "width": size[0],
                        "height": size[1],
                        "num_inference_steps": 20,
                        "guidance_scale": 7.5
                    }
                )
                
                # Handle response based on model type
                if response.get('response'):
                    # For text-to-image models that return base64 or file path
                    image_data = response.get('image_data')  # This would depend on actual API
                    
                    if image_data:
                        # Save generated image
                        image_path = self._save_chat_image(image_data, ".png")
                        
                        result.success = True
                        result.image_path = image_path
                        result.image_data = image_data
                        result.model_used = gen_model
                        
                        logger.info(f"Successfully generated image using {gen_model}")
                    else:
                        result.error = "No image data returned from generation model"
                else:
                    result.error = "Image generation failed"
                    
            except ImportError:
                result.error = "Ollama client not available for image generation"
            except Exception as api_error:
                result.error = f"Image generation API error: {str(api_error)}"
                
        except Exception as e:
            logger.error(f"Error in image generation: {e}")
            result.error = str(e)
            
        # Calculate generation time
        end_time = datetime.now()
        result.generation_time = (end_time - start_time).total_seconds()
        
        return result
    
    def process_image_paste(self, image_data: bytes, user_message: str = "") -> Dict[str, Any]:
        """
        Process an image pasted into chat with automatic analysis.
        
        Args:
            image_data: Raw image bytes from paste/upload
            user_message: Optional user message accompanying the image
            
        Returns:
            Dict containing analysis results and suggested response
        """
        logger.info(f"Processing pasted image with message: '{user_message[:50]}...'")
        
        # Determine analysis type based on user message
        analysis_type = "describe"  # Default
        custom_prompt = ""
        
        if user_message:
            message_lower = user_message.lower()
            if any(word in message_lower for word in ["text", "read", "ocr", "transcribe"]):
                analysis_type = "ocr"
            elif any(word in message_lower for word in ["object", "identify", "what", "list"]):
                analysis_type = "objects"
            elif any(word in message_lower for word in ["analyze", "explain", "tell me about"]):
                analysis_type = "custom"
                custom_prompt = user_message
        
        # Perform image analysis
        analysis_result = self.analyze_image_for_chat(
            image_data, 
            prompt=custom_prompt, 
            analysis_type=analysis_type
        )
        
        # Create chat response
        response_data = {
            "type": "image_analysis",
            "analysis_successful": analysis_result.success,
            "user_message": user_message,
            "analysis_type": analysis_type,
            "processing_time": analysis_result.processing_time
        }
        
        if analysis_result.success:
            # Create conversational response
            if analysis_type == "ocr" and analysis_result.extracted_text:
                chat_response = f"I can see text in this image:\n\n**Extracted Text:**\n{analysis_result.extracted_text}"
            elif analysis_result.description:
                chat_response = f"I can see this image shows:\n\n{analysis_result.description}"
            else:
                chat_response = "I've processed the image, but couldn't extract specific details."
                
            # Add context-aware follow-up
            if user_message:
                chat_response += f"\n\nRegarding your question '{user_message}': {analysis_result.description}"
            
            response_data.update({
                "chat_response": chat_response,
                "analysis_details": {
                    "description": analysis_result.description,
                    "extracted_text": analysis_result.extracted_text,
                    "confidence": analysis_result.confidence,
                    "model_used": analysis_result.model_used
                }
            })
        else:
            error_msg = analysis_result.error or "Unknown error"
            chat_response = f"I wasn't able to analyze this image properly. Error: {error_msg}"
            
            response_data.update({
                "chat_response": chat_response,
                "error": error_msg
            })
        
        return response_data
    
    def get_service_status(self) -> Dict[str, Any]:
        """Get comprehensive status of vision chat service."""
        vision_models = self._get_available_models("vision")
        gen_models = self._get_available_models("generation")
        
        return {
            "service_available": self.service_available,
            "image_analysis_available": len(vision_models) > 0,
            "image_generation_available": len(gen_models) > 0,
            "available_vision_models": vision_models,
            "available_generation_models": gen_models,
            "supported_image_formats": list(self.supported_image_formats),
            "max_image_size_mb": self.max_image_size_mb,
            "temp_image_dir": self.temp_image_dir,
            "fallback_ocr_available": image_analysis_available
        }
    
    def cleanup_old_images(self, max_age_hours: int = 24):
        """Clean up old temporary chat images."""
        try:
            import time
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600
            
            for filename in os.listdir(self.temp_image_dir):
                file_path = os.path.join(self.temp_image_dir, filename)
                if os.path.isfile(file_path):
                    file_age = current_time - os.path.getctime(file_path)
                    if file_age > max_age_seconds:
                        os.unlink(file_path)
                        logger.debug(f"Cleaned up old chat image: {filename}")
                        
        except Exception as e:
            logger.warning(f"Failed to cleanup old chat images: {e}")


# Singleton instance for global use
vision_chat_service = VisionChatService()


def analyze_chat_image(image_data: bytes, prompt: str = "", analysis_type: str = "describe") -> ImageAnalysisResult:
    """Convenience function for image analysis in chat."""
    return vision_chat_service.analyze_image_for_chat(image_data, prompt, analysis_type)


def generate_chat_image(prompt: str, style: str = "realistic", size: tuple = (512, 512)) -> ImageGenerationResult:
    """Convenience function for image generation in chat."""
    return vision_chat_service.generate_image_for_chat(prompt, style, size)


def process_pasted_image(image_data: bytes, user_message: str = "") -> Dict[str, Any]:
    """Convenience function for processing pasted images."""
    return vision_chat_service.process_image_paste(image_data, user_message)


def get_vision_chat_status() -> Dict[str, Any]:
    """Convenience function to get service status."""
    return vision_chat_service.get_service_status() 