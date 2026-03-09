"""
Semantic Intent Classifier Service

Uses SetFit (sentence-transformers fine-tuned classifier) to classify user queries
into intent categories: 'realtime' vs 'general'.

Supports ONNX export for fast inference. Falls back to keyword-based classification
if the model is unavailable.
"""

import logging
import os
from pathlib import Path
from typing import Tuple, Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Training data for intent classification
# Format: (query, label)
TRAINING_DATA = [
    # Real-time queries - require current/live data
    ("What's the lottery number tonight?", "realtime"),
    ("What are today's lottery results?", "realtime"),
    ("Current stock price of AAPL", "realtime"),
    ("What's Tesla stock at right now?", "realtime"),
    ("Weather in New York right now", "realtime"),
    ("What's the temperature outside?", "realtime"),
    ("Is it going to rain today?", "realtime"),
    ("Latest sports scores", "realtime"),
    ("Who won the game last night?", "realtime"),
    ("What's the score of the Lakers game?", "realtime"),
    ("Current bitcoin price", "realtime"),
    ("What's ethereum trading at?", "realtime"),
    ("Breaking news today", "realtime"),
    ("What happened in the news?", "realtime"),
    ("Current time in Tokyo", "realtime"),
    ("What time is it in London?", "realtime"),
    ("Live election results", "realtime"),
    ("Who's winning the election?", "realtime"),
    ("Flight status for AA123", "realtime"),
    ("Is my flight delayed?", "realtime"),
    ("Traffic conditions on I-95", "realtime"),
    ("How's traffic downtown?", "realtime"),
    ("Gold price today", "realtime"),
    ("Oil prices right now", "realtime"),
    ("Powerball numbers tonight", "realtime"),
    ("Mega Millions jackpot", "realtime"),
    ("What's trending on Twitter?", "realtime"),
    ("Who's trending today?", "realtime"),
    ("Live cryptocurrency prices", "realtime"),
    ("Current exchange rate USD to EUR", "realtime"),

    # General queries - can be answered from knowledge
    ("How do I write a Python function?", "general"),
    ("Explain quantum computing", "general"),
    ("What is the capital of France?", "general"),
    ("How does photosynthesis work?", "general"),
    ("Write a poem about nature", "general"),
    ("What's the difference between HTTP and HTTPS?", "general"),
    ("Explain machine learning", "general"),
    ("How do neural networks work?", "general"),
    ("What is recursion in programming?", "general"),
    ("Explain the theory of relativity", "general"),
    ("What causes earthquakes?", "general"),
    ("How do vaccines work?", "general"),
    ("What is the Pythagorean theorem?", "general"),
    ("Summarize World War 2", "general"),
    ("What are the planets in our solar system?", "general"),
    ("How does electricity work?", "general"),
    ("What is DNA?", "general"),
    ("Explain blockchain technology", "general"),
    ("What are prime numbers?", "general"),
    ("How do airplanes fly?", "general"),
    ("What is artificial intelligence?", "general"),
    ("Explain object-oriented programming", "general"),
    ("What is the water cycle?", "general"),
    ("How do computers work?", "general"),
    ("What is evolution?", "general"),
    ("Explain REST APIs", "general"),
    ("What is climate change?", "general"),
    ("How do antibiotics work?", "general"),
    ("What is the speed of light?", "general"),
    ("Explain Docker containers", "general"),
    ("Help me debug this code", "general"),
    ("Review my essay", "general"),
    ("Translate this to Spanish", "general"),
    ("What's a good recipe for pasta?", "general"),
    ("How can I improve my writing?", "general"),
]


class SemanticIntentClassifier:
    """
    SetFit-based semantic intent classifier.

    Classifies queries as 'realtime' (needs current data) or 'general' (knowledge-based).
    Falls back to keyword matching if model unavailable.
    """

    def __init__(self, model_path: Optional[str] = None):
        """
        Initialize the classifier.

        Args:
            model_path: Path to ONNX model file. If None, uses default location.
        """
        self._model = None
        self._tokenizer = None
        self._onnx_session = None
        self._use_onnx = False

        # Default model path
        if model_path is None:
            project_root = Path(__file__).parent.parent.parent
            model_path = str(project_root / "models" / "intent_classifier")

        self.model_path = model_path
        self.onnx_path = str(Path(model_path) / "model.onnx") if model_path else None

        # Try to load model
        self._load_model()

    def _load_model(self):
        """Load the SetFit model or ONNX runtime session."""
        # Try ONNX first (faster)
        if self.onnx_path and Path(self.onnx_path).exists():
            try:
                import onnxruntime as ort
                from transformers import AutoTokenizer

                self._onnx_session = ort.InferenceSession(
                    self.onnx_path,
                    providers=['CPUExecutionProvider']
                )

                # Load tokenizer for preprocessing
                tokenizer_path = Path(self.model_path)
                if (tokenizer_path / "tokenizer_config.json").exists():
                    self._tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_path))
                else:
                    # Fallback to base model tokenizer
                    self._tokenizer = AutoTokenizer.from_pretrained(
                        "sentence-transformers/all-MiniLM-L6-v2"
                    )

                self._use_onnx = True
                logger.info(f"Loaded ONNX intent classifier from {self.onnx_path}")
                return

            except ImportError:
                logger.warning("onnxruntime not installed, trying SetFit model")
            except Exception as e:
                logger.warning(f"Failed to load ONNX model: {e}")

        # Try SetFit model
        if self.model_path and Path(self.model_path).exists():
            try:
                from setfit import SetFitModel

                self._model = SetFitModel.from_pretrained(self.model_path)
                logger.info(f"Loaded SetFit intent classifier from {self.model_path}")
                return

            except ImportError:
                logger.warning("setfit not installed")
            except Exception as e:
                logger.warning(f"Failed to load SetFit model: {e}")

        logger.info("No intent classifier model available, using keyword fallback")

    def classify(self, query: str) -> Tuple[str, float]:
        """
        Classify a query's intent.

        Args:
            query: The user's query text

        Returns:
            Tuple of (intent_type, confidence)
            intent_type: 'realtime' or 'general'
            confidence: 0.0 to 1.0
        """
        if self._use_onnx and self._onnx_session:
            return self._classify_onnx(query)
        elif self._model:
            return self._classify_setfit(query)
        else:
            return self._classify_keywords(query)

    def _classify_onnx(self, query: str) -> Tuple[str, float]:
        """Classify using ONNX runtime."""
        try:
            # Tokenize
            inputs = self._tokenizer(
                query,
                padding=True,
                truncation=True,
                max_length=128,
                return_tensors="np"
            )

            # Run inference
            outputs = self._onnx_session.run(
                None,
                {
                    "input_ids": inputs["input_ids"],
                    "attention_mask": inputs["attention_mask"]
                }
            )

            # Get prediction (assuming logits output)
            logits = outputs[0][0]
            import numpy as np
            probs = np.exp(logits) / np.sum(np.exp(logits))

            # Assuming index 0 = general, index 1 = realtime
            pred_idx = np.argmax(probs)
            confidence = float(probs[pred_idx])

            intent = "realtime" if pred_idx == 1 else "general"
            return intent, confidence

        except Exception as e:
            logger.error(f"ONNX inference error: {e}")
            return self._classify_keywords(query)

    def _classify_setfit(self, query: str) -> Tuple[str, float]:
        """Classify using SetFit model."""
        try:
            prediction = self._model.predict([query])
            # SetFit returns string labels directly
            intent = str(prediction[0])

            # Get probabilities if available
            try:
                probs = self._model.predict_proba([query])
                confidence = float(max(probs[0]))
            except Exception:
                confidence = 0.85  # Default confidence for SetFit

            return intent, confidence

        except Exception as e:
            logger.error(f"SetFit inference error: {e}")
            return self._classify_keywords(query)

    def _classify_keywords(self, query: str) -> Tuple[str, float]:
        """Fallback keyword-based classification."""
        query_lower = query.lower()

        realtime_keywords = [
            'lottery', 'lotto', 'powerball', 'mega millions',
            'stock price', 'trading at', 'market',
            'weather', 'temperature', 'forecast', 'rain',
            'score', 'game', 'match', 'won', 'winning',
            'bitcoin', 'crypto', 'ethereum', 'price today',
            'news', 'breaking', 'latest', 'current',
            'right now', 'today', 'tonight', 'live',
            'traffic', 'flight status', 'delayed',
            'trending', 'viral', 'election results'
        ]

        matches = sum(1 for kw in realtime_keywords if kw in query_lower)

        if matches >= 2:
            return "realtime", min(0.9, 0.5 + matches * 0.15)
        elif matches == 1:
            return "realtime", 0.6
        else:
            return "general", 0.7

    @classmethod
    def train_and_save(
        cls,
        output_path: str,
        training_data: Optional[List[Tuple[str, str]]] = None,
        base_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        export_onnx: bool = True
    ) -> "SemanticIntentClassifier":
        """
        Train a new SetFit classifier and save it.

        Args:
            output_path: Directory to save the model
            training_data: List of (text, label) tuples. Uses default if None.
            base_model: Base sentence-transformer model
            export_onnx: Whether to export ONNX version

        Returns:
            Trained SemanticIntentClassifier instance
        """
        try:
            from setfit import SetFitModel, Trainer, TrainingArguments
            from datasets import Dataset
        except ImportError:
            raise ImportError("Please install setfit: pip install setfit")

        # Use default training data if not provided
        if training_data is None:
            training_data = TRAINING_DATA

        # Prepare dataset
        texts = [t[0] for t in training_data]
        labels = [t[1] for t in training_data]

        dataset = Dataset.from_dict({
            "text": texts,
            "label": labels
        })

        # Create and train model
        logger.info(f"Training SetFit model with {len(training_data)} examples...")

        model = SetFitModel.from_pretrained(base_model)

        args = TrainingArguments(
            batch_size=16,
            num_epochs=1,
            evaluation_strategy="epoch",
            save_strategy="epoch",
            load_best_model_at_end=True,
        )

        trainer = Trainer(
            model=model,
            args=args,
            train_dataset=dataset,
        )

        trainer.train()

        # Save model
        output_dir = Path(output_path)
        output_dir.mkdir(parents=True, exist_ok=True)

        model.save_pretrained(str(output_dir))
        logger.info(f"Saved SetFit model to {output_dir}")

        # Export to ONNX if requested
        if export_onnx:
            try:
                cls._export_to_onnx(model, output_dir)
            except Exception as e:
                logger.warning(f"ONNX export failed: {e}")

        return cls(str(output_dir))

    @staticmethod
    def _export_to_onnx(model, output_dir: Path):
        """Export SetFit model to ONNX format."""
        try:
            import torch
            from transformers import AutoTokenizer

            onnx_path = output_dir / "model.onnx"

            # Get the sentence transformer body
            st_model = model.model_body

            # Create dummy input
            tokenizer = AutoTokenizer.from_pretrained(
                "sentence-transformers/all-MiniLM-L6-v2"
            )
            dummy_input = tokenizer(
                "dummy text",
                padding=True,
                truncation=True,
                max_length=128,
                return_tensors="pt"
            )

            # Export
            torch.onnx.export(
                st_model,
                (dummy_input["input_ids"], dummy_input["attention_mask"]),
                str(onnx_path),
                input_names=["input_ids", "attention_mask"],
                output_names=["embeddings"],
                dynamic_axes={
                    "input_ids": {0: "batch", 1: "sequence"},
                    "attention_mask": {0: "batch", 1: "sequence"},
                    "embeddings": {0: "batch"}
                },
                opset_version=14
            )

            # Save tokenizer
            tokenizer.save_pretrained(str(output_dir))

            logger.info(f"Exported ONNX model to {onnx_path}")

        except Exception as e:
            logger.error(f"ONNX export error: {e}")
            raise

    def is_model_loaded(self) -> bool:
        """Check if a trained model is loaded (vs keyword fallback)."""
        return self._model is not None or self._onnx_session is not None

    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the loaded model."""
        return {
            "model_loaded": self.is_model_loaded(),
            "model_path": self.model_path,
            "using_onnx": self._use_onnx,
            "using_setfit": self._model is not None,
            "using_keyword_fallback": not self.is_model_loaded()
        }


# Global singleton instance
_intent_classifier: Optional[SemanticIntentClassifier] = None


def get_intent_classifier() -> SemanticIntentClassifier:
    """Get the global semantic intent classifier instance."""
    global _intent_classifier
    if _intent_classifier is None:
        _intent_classifier = SemanticIntentClassifier()
    return _intent_classifier


def classify_intent(query: str) -> Tuple[str, float]:
    """
    Classify a query's intent.

    Convenience function that uses the global classifier.

    Args:
        query: The user's query text

    Returns:
        Tuple of (intent_type, confidence)
    """
    return get_intent_classifier().classify(query)
