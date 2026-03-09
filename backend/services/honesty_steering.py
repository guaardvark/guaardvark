"""
Honesty Steering Service

Implements activation steering for enhanced honesty in LLM responses,
particularly for queries requiring real-time data where the model
should admit uncertainty rather than hallucinate.

Uses steering-vectors library when available, with fallback to
prompt-based steering for quantized models (which don't support
activation manipulation).
"""

import logging
from typing import Dict, Any, Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class HonestySteering:
    """
    Manages honesty steering for LLM responses.

    Two modes of operation:
    1. Activation steering (when available) - directly modifies model activations
    2. Prompt-based steering (fallback) - uses carefully crafted prompts

    The prompt-based fallback is the primary mode for Ollama/quantized models
    since activation steering requires access to model internals.
    """

    def __init__(self, vector_path: Optional[str] = None):
        """
        Initialize the honesty steering service.

        Args:
            vector_path: Path to pre-computed steering vector file
        """
        self._steering_vector = None
        self._activation_steering_available = False

        # Default vector path
        if vector_path is None:
            project_root = Path(__file__).parent.parent.parent
            vector_path = str(project_root / "models" / "steering_vectors" / "honesty.pt")

        self.vector_path = vector_path

        # Try to load steering vector
        self._try_load_vector()

        # Honesty prompt templates for different scenarios
        self._honesty_prompts = self._build_honesty_prompts()

    def _try_load_vector(self):
        """Attempt to load pre-computed steering vector."""
        if not Path(self.vector_path).exists():
            logger.info("No steering vector file found, using prompt-based steering")
            return

        try:
            # Try to import steering_vectors library
            from steering_vectors import SteeringVector
            import torch

            self._steering_vector = torch.load(self.vector_path)
            self._activation_steering_available = True
            logger.info(f"Loaded steering vector from {self.vector_path}")

        except ImportError:
            logger.info("steering_vectors library not installed, using prompt-based steering")
        except Exception as e:
            logger.warning(f"Failed to load steering vector: {e}")

    def _build_honesty_prompts(self) -> Dict[str, str]:
        """Build honesty prompt templates for different scenarios."""
        return {
            # For real-time data queries (lottery, stocks, weather, etc.)
            "realtime": """CRITICAL HONESTY REQUIREMENT:
The user is asking about REAL-TIME data that changes frequently (lottery numbers, stock prices, weather, sports scores, etc.).

You MUST:
1. Clearly state that you cannot access real-time data
2. Explain that your training data has a cutoff date
3. Suggest reliable sources where they can find current information
4. NEVER guess, estimate, or make up numbers/data

Example response pattern:
"I don't have access to real-time [lottery results/stock prices/weather/etc.]. My knowledge has a cutoff date and I cannot browse the internet. For current [data type], please check [relevant source like official lottery website, financial news, weather service]."

DO NOT provide any specific numbers, dates, or values for real-time data - always redirect to authoritative sources.""",

            # For factual queries where model might be uncertain
            "uncertain": """HONESTY GUIDELINE:
When responding to factual questions:
- If you're not confident about a fact, say "I'm not certain about this"
- If information might be outdated, note that your knowledge has a cutoff
- Don't present speculation as fact
- It's better to say "I don't know" than to guess incorrectly""",

            # For general enhanced honesty
            "general": """HONESTY REMINDER:
- Only state facts you're confident about
- Acknowledge uncertainty when present
- Don't make up citations, URLs, or specific data
- Correct yourself if you realize you made an error""",

            # Minimal honesty nudge (least intrusive)
            "minimal": """Be accurate and honest. Say "I don't know" if uncertain."""
        }

    def get_steering_prompt(
        self,
        intent: str,
        intensity: str = "standard"
    ) -> str:
        """
        Get the appropriate honesty steering prompt.

        Args:
            intent: Query intent type ('realtime', 'general', etc.)
            intensity: How strong the steering should be
                      ('minimal', 'standard', 'strong')

        Returns:
            Steering prompt text to prepend to system prompt
        """
        # Map intensity to prompt key
        if intent == "realtime":
            # Always use full realtime prompt for real-time queries
            return self._honesty_prompts["realtime"]
        elif intensity == "minimal":
            return self._honesty_prompts["minimal"]
        elif intensity == "strong":
            return self._honesty_prompts["uncertain"]
        else:
            return self._honesty_prompts["general"]

    def apply_steering(
        self,
        model: Any,
        intent: str,
        coefficient: float = 0.3
    ) -> bool:
        """
        Apply activation steering to a model.

        NOTE: This only works with full-precision models that expose
        their internal activations. Will NOT work with:
        - Ollama models (API-based)
        - Quantized models (GGUF, GPTQ, etc.)
        - Any API-based LLM (OpenAI, Anthropic, etc.)

        For these cases, use get_steering_prompt() instead.

        Args:
            model: The model instance to steer
            intent: Query intent type
            coefficient: Steering strength (0.0-1.0)

        Returns:
            True if steering was applied, False if falling back to prompts
        """
        if not self._activation_steering_available:
            logger.debug("Activation steering not available, use prompt-based steering")
            return False

        if intent != "realtime":
            # Only apply activation steering for real-time queries
            return False

        try:
            from steering_vectors import apply_steering_vector

            # Apply the pre-computed honesty/refusal vector
            # This increases the model's tendency to refuse/acknowledge uncertainty
            apply_steering_vector(
                model,
                self._steering_vector,
                coefficient=coefficient,
                layer_range=(10, 20)  # Middle layers typically work best
            )

            logger.debug(f"Applied activation steering with coefficient {coefficient}")
            return True

        except Exception as e:
            logger.warning(f"Failed to apply activation steering: {e}")
            return False

    def should_use_activation_steering(self, model_info: Dict[str, Any]) -> bool:
        """
        Determine if activation steering can be used for a given model.

        Args:
            model_info: Information about the model being used

        Returns:
            True if activation steering is possible and recommended
        """
        if not self._activation_steering_available:
            return False

        # Check model type
        model_type = model_info.get("type", "").lower()
        quantized = model_info.get("quantized", False)
        api_based = model_info.get("api_based", True)  # Default to API-based

        # Activation steering requires full model access
        if api_based:
            return False
        if quantized:
            return False
        if "gguf" in model_type or "gptq" in model_type:
            return False

        return True

    def get_enhanced_system_prompt(
        self,
        base_prompt: str,
        intent: str,
        intensity: str = "standard"
    ) -> str:
        """
        Enhance a system prompt with honesty steering.

        This is the primary method to use with Ollama and quantized models.

        Args:
            base_prompt: The original system prompt
            intent: Query intent type
            intensity: Steering intensity

        Returns:
            Enhanced system prompt with honesty guidance
        """
        steering_prompt = self.get_steering_prompt(intent, intensity)

        if not steering_prompt:
            return base_prompt

        # Prepend steering to base prompt
        return f"{steering_prompt}\n\n---\n\n{base_prompt}"

    def is_activation_steering_available(self) -> bool:
        """Check if activation steering is available."""
        return self._activation_steering_available

    def get_status(self) -> Dict[str, Any]:
        """Get current status of honesty steering service."""
        return {
            "activation_steering_available": self._activation_steering_available,
            "vector_path": self.vector_path,
            "vector_loaded": self._steering_vector is not None,
            "prompt_steering_available": True,  # Always available as fallback
            "available_intensities": ["minimal", "standard", "strong"],
            "supported_intents": list(self._honesty_prompts.keys())
        }


# Pre-computed steering vector generation (run offline)
def compute_honesty_vector(
    model_name: str = "meta-llama/Llama-2-7b-hf",
    output_path: str = None,
    dataset: str = "PKU-Alignment/BeaverTails"
) -> Optional[str]:
    """
    Compute a honesty/refusal steering vector from the BeaverTails dataset.

    This should be run OFFLINE on a machine with GPU and full model access.
    The resulting vector can then be loaded for inference.

    NOTE: This requires:
    - steering_vectors library
    - transformers library
    - torch
    - GPU with sufficient VRAM
    - Access to the base model (non-quantized)

    Args:
        model_name: HuggingFace model to use for computing vectors
        output_path: Where to save the resulting vector
        dataset: Dataset to use for contrastive examples

    Returns:
        Path to saved vector, or None if failed
    """
    try:
        from steering_vectors import train_steering_vector
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from datasets import load_dataset
        import torch

        logger.info(f"Computing honesty vector using {model_name}...")

        # Set default output path
        if output_path is None:
            project_root = Path(__file__).parent.parent.parent
            output_path = str(project_root / "models" / "steering_vectors" / "honesty.pt")

        # Load model
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch.float16,
            device_map="auto"
        )
        tokenizer = AutoTokenizer.from_pretrained(model_name)

        # Load BeaverTails dataset
        ds = load_dataset(dataset, split="train[:1000]")

        # Create contrastive pairs (safe vs unsafe responses)
        positive_examples = []  # Honest/refusing responses
        negative_examples = []  # Potentially hallucinating responses

        for item in ds:
            if item.get("is_safe", True):
                positive_examples.append(item["response"])
            else:
                negative_examples.append(item["response"])

        # Compute steering vector
        vector = train_steering_vector(
            model=model,
            tokenizer=tokenizer,
            positive_examples=positive_examples[:200],
            negative_examples=negative_examples[:200],
            layers=list(range(10, 21))  # Middle layers
        )

        # Save vector
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)
        torch.save(vector, output_path)

        logger.info(f"Saved honesty vector to {output_path}")
        return output_path

    except ImportError as e:
        logger.error(f"Missing required library: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to compute honesty vector: {e}")
        return None


# Global singleton instance
_honesty_steerer: Optional[HonestySteering] = None


def get_honesty_steerer() -> HonestySteering:
    """Get the global honesty steering service instance."""
    global _honesty_steerer
    if _honesty_steerer is None:
        _honesty_steerer = HonestySteering()
    return _honesty_steerer


def get_steering_prompt(intent: str, intensity: str = "standard") -> str:
    """
    Get honesty steering prompt for an intent.

    Convenience function using global steerer.
    """
    return get_honesty_steerer().get_steering_prompt(intent, intensity)


def enhance_prompt_for_honesty(
    base_prompt: str,
    intent: str,
    intensity: str = "standard"
) -> str:
    """
    Enhance a system prompt with honesty steering.

    Convenience function using global steerer.
    """
    return get_honesty_steerer().get_enhanced_system_prompt(base_prompt, intent, intensity)
