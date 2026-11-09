#!/usr/bin/env python3
"""
GUAARDVARK Model Fine-Tuner
Uses unsloth for efficient LoRA fine-tuning on consumer GPUs.

The backend runs this file as its own process (backend/services/training_runner.py):

    python finetune_model.py run --spec <spec.json>    train, reporting on stdout
    python finetune_model.py merge --spec <spec.json>  fold an adapter into its base
    python finetune_model.py check --json              what the trainer can load

Lines starting with EVENT_PREFIX on stdout are JSON events for the backend;
everything else is the training log.
"""

import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path

TRAINING_DIR = Path(os.environ.get('GUAARDVARK_ROOT', '.')) / "training"
DATASETS_DIR = TRAINING_DIR / "datasets"
MODELS_DIR = TRAINING_DIR / "models"

EVENT_PREFIX = "@@guaardvark-train "

# What `run --spec` reads; training_tasks builds the spec from these names only.
SPEC_KEYS = (
    "base_model",            # local snapshot folder of the base model
    "base_model_id",         # its declared id, for the log
    "data_path",
    "eval_data_path",
    "output_dir",
    "max_steps",
    "learning_rate",
    "batch_size",
    "gradient_accumulation_steps",
    "lora_rank",
    "max_seq_length",
    "resume",
    "load_in_4bit",
    "response_markers",      # {"instruction": ..., "response": ...} or None
    "parent_pid",
)

# What `merge --spec` reads.
MERGE_SPEC_KEYS = ("base_model", "lora_dir", "out_dir", "parent_pid")

try:
    from . import dataset_formats
except ImportError:  # run as a script, or loaded outside the backend package
    _HERE = str(Path(__file__).resolve().parent)
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    import dataset_formats


def emit(event: str, **fields):
    """One JSON event line for the backend."""
    print(EVENT_PREFIX + json.dumps({"event": event, **fields}, default=str), flush=True)


def check_dependencies():
    """Check if required packages are installed."""
    missing = []

    try:
        import torch
        print(f"PyTorch: {torch.__version__}, CUDA: {torch.cuda.is_available()}")
    except ImportError:
        missing.append("torch")

    # Looked up, not imported: importing Unsloth without a GPU probes the
    # network (require_cuda); finetune() imports it after that check.
    import importlib.util
    if importlib.util.find_spec("unsloth") is not None:
        print("Unsloth: installed")
    else:
        missing.append("unsloth")

    try:
        from datasets import load_dataset  # noqa: F401
        print("Datasets: installed")
    except ImportError:
        missing.append("datasets")

    try:
        from trl import SFTTrainer  # noqa: F401
        print("TRL: installed")
    except ImportError:
        missing.append("trl")

    if missing:
        print(f"\nMissing packages: {missing}")
        print("Install them in Settings > Training libraries.")
        return False

    return True


def _fields(cls) -> set:
    """Field names a config class or callable accepts."""
    import dataclasses
    import inspect

    if dataclasses.is_dataclass(cls):
        return {f.name for f in dataclasses.fields(cls)}
    try:
        return set(inspect.signature(cls).parameters)
    except (TypeError, ValueError):
        return set()


def require_cuda() -> None:
    """Stop before Unsloth loads when no CUDA GPU is visible.

    Without one, Unsloth's import fails anyway, and on the way its error hint
    probes download.pytorch.org for a ROCm wheel index. Failing here gives a
    plain reason and makes no network call.
    """
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError(
            "No CUDA GPU is visible to the trainer "
            f"(CUDA_VISIBLE_DEVICES={os.environ.get('CUDA_VISIBLE_DEVICES')!r}); "
            "Unsloth trains on an NVIDIA GPU only."
        )


def is_multimodal(base_model: str) -> bool:
    """Whether a checkpoint carries image or audio towers beside its language
    model (Gemma 4, for one), read from its config.json."""
    try:
        config = json.loads((Path(base_model) / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(config.get("vision_config") or config.get("audio_config"))


def text_tokenizer(tokenizer):
    """The text tokenizer inside a multimodal checkpoint's processor, given
    the processor's chat template when it has none of its own."""
    from transformers import ProcessorMixin

    if not isinstance(tokenizer, ProcessorMixin):
        return tokenizer
    inner = tokenizer.tokenizer
    if not getattr(inner, "chat_template", None) and getattr(tokenizer, "chat_template", None):
        inner.chat_template = tokenizer.chat_template
    return inner


def check_report() -> dict:
    """Library versions, CUDA, bf16 and the TRL interface this trainer uses.

    ``ok`` is False with ``problems`` when the trainer could not run here."""
    import importlib.metadata as metadata

    report = {"ok": False, "python": sys.version.split()[0], "libraries": {}, "problems": []}
    for dist in ("torch", "transformers", "peft", "accelerate", "trl", "datasets",
                 "unsloth", "unsloth-zoo", "bitsandbytes"):
        try:
            report["libraries"][dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            report["libraries"][dist] = None

    # Library banners go to stderr so stdout carries only the report.
    with contextlib.redirect_stdout(sys.stderr):
        try:
            import torch
            report["cuda"] = bool(torch.cuda.is_available())
            report["cuda_device"] = torch.cuda.get_device_name(0) if report["cuda"] else None
            report["bf16"] = bool(report["cuda"] and torch.cuda.is_bf16_supported())
        except Exception as e:
            report["problems"].append(f"torch: {type(e).__name__}: {e}")
        cuda_ok = False
        try:
            require_cuda()
            cuda_ok = True
            import unsloth  # noqa: F401  (patches transformers and TRL on import)
            from unsloth import FastLanguageModel  # noqa: F401
        except Exception as e:
            report["problems"].append(f"unsloth: {type(e).__name__}: {e}")
        try:
            from trl import SFTConfig, SFTTrainer
            fields = _fields(SFTConfig)
            report["sft_config_fields"] = sorted(
                f for f in fields if f in ("max_length", "max_seq_length", "dataset_text_field",
                                           "completion_only_loss", "assistant_only_loss", "packing",
                                           "report_to", "bf16", "fp16"))
            report["sft_trainer_params"] = sorted(
                p for p in _fields(SFTTrainer.__init__) if p in ("processing_class", "tokenizer"))
            if not ({"max_length", "max_seq_length"} & fields):
                report["problems"].append("trl: SFTConfig has neither max_length nor max_seq_length")
        except Exception as e:
            report["problems"].append(f"trl: {type(e).__name__}: {e}")
        # Any unsloth import probes the network without a GPU (require_cuda).
        report["train_on_responses_only"] = False
        if cuda_ok:
            try:
                from unsloth.chat_templates import train_on_responses_only  # noqa: F401
                report["train_on_responses_only"] = True
            except Exception:
                pass
        try:
            from datasets import Dataset  # noqa: F401
        except Exception as e:
            report["problems"].append(f"datasets: {type(e).__name__}: {e}")
    if not report.get("cuda"):
        report["problems"].append("CUDA is not available to the trainer")
    report["ok"] = not report["problems"]
    return report


def load_training_data(data_path: str, tokenizer):
    """Training rows rendered to text, as a datasets.Dataset with one "text" column.

    Returns (dataset, report); report counts rows read, rows used and rows
    skipped, and says whether every row is a conversation."""
    from datasets import Dataset

    errors = []
    rows = list(dataset_formats.iter_rows(str(data_path), errors))
    texts, report = render_rows(rows, tokenizer)
    report["unreadable"] = len(errors)
    return Dataset.from_list([{"text": t} for t in texts]), report


def render_rows(rows, tokenizer):
    """Render dataset rows to training text with the base model's chat template.

    Conversation rows go through tokenizer.apply_chat_template, so the model
    learns the turn markers it is prompted with at inference; plain ``text``
    rows are used as written. Returns (texts, report)."""
    texts = []
    report = {"rows": len(rows), "used": 0, "skipped": 0, "text_rows": 0, "chat_rows": 0}
    template = getattr(tokenizer, "chat_template", None)
    bos = _added_bos(tokenizer)
    for row in rows:
        fmt = dataset_formats.detect_format(row)
        if fmt == "text":
            if row["text"].strip():
                texts.append(row["text"])
                report["text_rows"] += 1
            else:
                report["skipped"] += 1
            continue
        messages, reason = dataset_formats.to_messages(row)
        if reason:
            report["skipped"] += 1
            continue
        if not template:
            raise ValueError("The base model's tokenizer has no chat template, so conversation "
                             "rows cannot be rendered. Choose a base model with a chat template.")
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
        if not isinstance(text, str):
            raise ValueError("The base model's chat template did not return text")
        if bos and text.startswith(bos):
            # The template writes BOS and encoding adds another; keep one.
            text = text[len(bos):]
        texts.append(text)
        report["chat_rows"] += 1
    report["used"] = len(texts)
    return texts, report


def _added_bos(tokenizer):
    """The BOS text the tokenizer puts in front of every encoding, or None."""
    bos, bos_id = getattr(tokenizer, "bos_token", None), getattr(tokenizer, "bos_token_id", None)
    if not bos or bos_id is None:
        return None
    try:
        ids = tokenizer(text="x", add_special_tokens=True)["input_ids"]
    except Exception:
        return None
    return bos if list(ids[:1]) == [bos_id] else None


def find_last_checkpoint(output_dir: Path) -> str:
    """
    Find the most recent checkpoint in the output directory.

    Args:
        output_dir: Path to the training output directory

    Returns:
        Path to the last checkpoint, or None if no checkpoints exist
    """
    checkpoint_dir = output_dir / "checkpoints"
    if not checkpoint_dir.exists():
        return None

    # Find all checkpoint directories (named checkpoint-{step})
    checkpoints = []
    for item in checkpoint_dir.iterdir():
        if item.is_dir() and item.name.startswith("checkpoint-"):
            try:
                step = int(item.name.split("-")[1])
                checkpoints.append((step, item))
            except (IndexError, ValueError):
                continue

    if not checkpoints:
        return None

    # Sort by step number and return the highest
    checkpoints.sort(key=lambda x: x[0], reverse=True)
    last_checkpoint = checkpoints[0][1]

    print(f"Found checkpoint at step {checkpoints[0][0]}: {last_checkpoint}")
    return str(last_checkpoint)


def heldout_losses(model, tokenizer, texts, max_seq_length):
    """Mean per-token loss on held-out texts for the trained adapter and for
    the base model (the same weights with the adapter switched off).

    Rows go through one at a time, so the measurement needs no more memory
    than a single training row."""
    import torch

    device = model.get_input_embeddings().weight.device

    def mean_loss():
        total, tokens = 0.0, 0
        for text in texts:
            enc = tokenizer(text=text, return_tensors="pt", truncation=True,
                            max_length=max_seq_length, add_special_tokens=True)
            input_ids = enc["input_ids"].to(device)
            predicted = int(input_ids.shape[1]) - 1
            if predicted < 1:
                continue
            attention_mask = enc.get("attention_mask")
            if attention_mask is not None:
                attention_mask = attention_mask.to(device)
            out = model(input_ids=input_ids, attention_mask=attention_mask, labels=input_ids)
            total += float(out.loss) * predicted
            tokens += predicted
        return total / tokens if tokens else float("nan")

    was_training = model.training
    model.eval()
    try:
        with torch.no_grad():
            adapter_loss = mean_loss()
            with model.disable_adapter():
                base_loss = mean_loss()
    finally:
        model.train(was_training)
    return {"adapter_loss": adapter_loss, "base_loss": base_loss, "rows": len(texts)}


def sft_config_kwargs(config_fields: set, *, output_dir: Path, max_steps: int, learning_rate: float,
                      batch_size: int, gradient_accumulation_steps: int, max_seq_length: int,
                      bf16: bool) -> dict:
    """SFTConfig arguments for the installed TRL, named the way it names them."""
    values = {
        "per_device_train_batch_size": batch_size,
        "gradient_accumulation_steps": gradient_accumulation_steps,
        "warmup_steps": min(10, max(1, max_steps // 10)),
        "max_steps": max_steps,
        "learning_rate": learning_rate,
        "bf16": bf16,
        "fp16": not bf16,
        "logging_steps": max(1, min(10, max_steps // 10)),
        "output_dir": str(output_dir / "checkpoints"),
        "optim": "paged_adamw_8bit",
        "save_steps": 100,
        "save_total_limit": 3,
        "report_to": "none",
        "seed": 42,
        "dataset_text_field": "text",
    }
    values["max_length" if "max_length" in config_fields else "max_seq_length"] = max_seq_length
    return {k: v for k, v in values.items() if k in config_fields}


def progress_callbacks(progress_callback, total_steps) -> list:
    """Trainer callbacks that report progress to progress_callback as
    (step, total_steps, loss, metrics): on every log line, and between logs at
    most every two seconds. Empty when there is no progress_callback. The text
    and vision trainers share it."""
    if not progress_callback:
        return []
    from transformers import TrainerCallback

    class ProgressCallback(TrainerCallback):
        def __init__(self, callback_func, total_steps):
            self.callback_func = callback_func
            self.total_steps = total_steps
            self.last_loss = None
            self.last_sent = 0.0

        def _send(self, state, logs=None):
            metrics = {
                "loss": (logs or {}).get("loss", self.last_loss),
                "learning_rate": (logs or {}).get("learning_rate"),
                "epoch": getattr(state, "epoch", None),
            }
            self.last_sent = time.monotonic()
            self.callback_func(state.global_step, self.total_steps, metrics["loss"], metrics)

        def on_log(self, args, state, control, logs=None, **kwargs):
            if logs and "loss" in logs:
                self.last_loss = logs["loss"]
            if state.global_step:
                self._send(state, logs)

        def on_step_end(self, args, state, control, **kwargs):
            if time.monotonic() - self.last_sent >= 2.0:
                self._send(state)

    return [ProgressCallback(progress_callback, total_steps)]


def finetune(
    base_model: str,
    data_path: str,
    output_name: str = None,
    max_steps: int = 500,
    learning_rate: float = 2e-4,
    batch_size: int = 2,
    lora_rank: int = 16,
    max_seq_length: int = 2048,
    gradient_accumulation_steps: int = 4,
    freeze_vision: bool = True,
    progress_callback: callable = None,
    resume: bool = False,
    eval_data_path: str = None,
    eval_callback: callable = None,
    output_dir: str = None,
    load_in_4bit: bool = True,
    response_markers: dict = None,
    dataset_callback: callable = None,
):
    """
    Fine-tune a model with LoRA.

    Args:
        base_model: Base model to fine-tune (local folder, or a Hugging Face id already cached)
        data_path: Path to training data (JSONL or JSON, any dataset_formats shape)
        output_name: Name for output model (under MODELS_DIR) when output_dir is not given
        max_steps: Maximum training steps
        learning_rate: Learning rate
        batch_size: Per-device batch size
        lora_rank: LoRA adapter rank
        max_seq_length: Maximum sequence length; longer rows are left out
        gradient_accumulation_steps: Gradient accumulation steps
        freeze_vision: Freeze vision tower (for multimodal models)
        progress_callback: Called as (step, total_steps, loss, metrics)
        resume: If True, resume from last checkpoint if available
        eval_data_path: Held-out rows (same format as data_path), never trained on
        eval_callback: Called after training with heldout_losses() output, or
            with {"reason": ...} when the held-out loss could not be measured
        output_dir: Folder for checkpoints and the adapter
        load_in_4bit: Load the base model quantised to 4-bit
        response_markers: {"instruction": ..., "response": ...} text that opens a
            user and an assistant turn in the chat template; when given and
            every row is a conversation, loss is taken on the replies only
        dataset_callback: Called with the dataset report before training
    """

    require_cuda()
    from unsloth import FastLanguageModel
    from trl import SFTConfig, SFTTrainer

    output_name = output_name or f"guaardvark-{base_model.replace('/', '-').replace(':', '-')}"
    output_dir = Path(output_dir) if output_dir else MODELS_DIR / output_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check for existing checkpoint if resuming
    resume_checkpoint = None
    if resume:
        resume_checkpoint = find_last_checkpoint(output_dir)
        if resume_checkpoint:
            print("\n=== RESUMING FROM CHECKPOINT ===")
            print(f"Checkpoint: {resume_checkpoint}")
        else:
            print("\nNo checkpoint found, starting fresh training.")

    print("\n=== GUAARDVARK Fine-Tuning ===")
    print(f"Base model: {base_model}")
    print(f"Training data: {data_path}")
    print(f"Output: {output_dir}")
    print(f"Max steps: {max_steps}")
    print(f"Max seq length: {max_seq_length}")
    print(f"Batch size: {batch_size}")
    print(f"Gradient accumulation steps: {gradient_accumulation_steps}")
    print(f"LoRA rank: {lora_rank}")
    print(f"4-bit: {load_in_4bit}")
    print(f"Freeze Vision Tower: {freeze_vision}")

    multimodal = is_multimodal(base_model)
    if multimodal:
        from unsloth import FastModel
        print("Multimodal checkpoint: LoRA on the language layers only")
        model, processor = FastModel.from_pretrained(
            model_name=base_model,
            max_seq_length=max_seq_length,
            dtype=None,
            load_in_4bit=load_in_4bit,
        )
        # The image and audio towers keep their weights and take no gradients.
        model = FastModel.get_peft_model(
            model,
            r=lora_rank,
            finetune_vision_layers=False,
            finetune_language_layers=True,
            finetune_attention_modules=True,
            finetune_mlp_modules=True,
            lora_alpha=lora_rank,
            lora_dropout=0,
            bias="none",
            use_gradient_checkpointing=True,
            random_state=42,
        )
        tokenizer = text_tokenizer(processor)
    else:
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=base_model,
            max_seq_length=max_seq_length,
            dtype=None,
            load_in_4bit=load_in_4bit,
        )
        processor = tokenizer

        model = FastLanguageModel.get_peft_model(
            model,
            r=lora_rank,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                            "gate_proj", "up_proj", "down_proj"],
            lora_alpha=lora_rank,
            lora_dropout=0,
            bias="none",
            use_gradient_checkpointing=True,
            random_state=42,
        )

    import torch
    import gc
    if torch.cuda.is_available():
        print(f"GPU Memory after loading model: {torch.cuda.memory_allocated() / 1024**3:.2f} GB")

    # Freeze vision tower if requested
    if freeze_vision:
        vision_tower = None
        if hasattr(model, "model") and hasattr(model.model, "vision_tower"):
            vision_tower = model.model.vision_tower
        elif hasattr(model, "base_model") and hasattr(model.base_model, "model") and hasattr(model.base_model.model, "vision_tower"):
            vision_tower = model.base_model.model.vision_tower

        if vision_tower:
            print("Freezing vision tower to save memory...")
            vision_tower.requires_grad_(False)
            vision_tower.eval()
            for param in vision_tower.parameters():
                param.requires_grad = False

    dataset, data_report = load_training_data(data_path, tokenizer)

    def fits(example):
        ids = tokenizer(example["text"], truncation=False, add_special_tokens=True)["input_ids"]
        return len(ids) <= max_seq_length

    print("Filtering dataset based on token length...")
    before = len(dataset)
    dataset = dataset.filter(fits)
    data_report["too_long"] = before - len(dataset)
    data_report["trained"] = len(dataset)
    if data_report["too_long"]:
        print(f"Left out {data_report['too_long']} rows longer than max_seq_length {max_seq_length}.")
    if len(dataset) == 0:
        raise ValueError(f"No row can be trained on: {data_report['rows']} read, "
                         f"{data_report['skipped']} not usable, {data_report['too_long']} longer than "
                         f"max_seq_length {max_seq_length}.")

    responses_only = bool(response_markers) and data_report["text_rows"] == 0
    train_on_responses_only = None
    if responses_only:
        try:
            from unsloth.chat_templates import train_on_responses_only
        except ImportError:
            responses_only = False
    data_report["loss_on"] = "assistant replies" if responses_only else "whole text"
    print(f"Training examples: {len(dataset)} (loss on {data_report['loss_on']})")
    if dataset_callback:
        dataset_callback(dict(data_report))

    eval_texts = None
    if eval_data_path:
        eval_dataset, _ = load_training_data(eval_data_path, tokenizer)
        eval_dataset = eval_dataset.filter(fits)
        eval_texts = list(eval_dataset["text"])
        print(f"Held-out examples: {len(eval_texts)}")

    try:
        from unsloth import is_bf16_supported
        bf16 = bool(is_bf16_supported())
    except ImportError:
        bf16 = bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported())

    config = SFTConfig(**sft_config_kwargs(
        _fields(SFTConfig), output_dir=output_dir, max_steps=max_steps, learning_rate=learning_rate,
        batch_size=batch_size, gradient_accumulation_steps=gradient_accumulation_steps,
        max_seq_length=max_seq_length, bf16=bf16))

    callbacks = progress_callbacks(progress_callback, max_steps)

    trainer_kwargs = {"model": model, "train_dataset": dataset, "args": config,
                      "callbacks": callbacks or None}
    trainer_params = _fields(SFTTrainer.__init__)
    if "processing_class" in trainer_params:
        trainer_kwargs["processing_class"] = tokenizer
    else:
        trainer_kwargs["tokenizer"] = tokenizer
    trainer = SFTTrainer(**trainer_kwargs)

    if responses_only:
        trainer = train_on_responses_only(
            trainer,
            instruction_part=response_markers["instruction"],
            response_part=response_markers["response"],
        )

    print("\nClearing GPU cache before training...")
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        print(f"GPU Memory before train: {torch.cuda.memory_allocated() / 1024**3:.2f} GB")

    print("\nStarting training...")
    if resume_checkpoint:
        print(f"Resuming from: {resume_checkpoint}")
        trainer.train(resume_from_checkpoint=resume_checkpoint)
    else:
        trainer.train()

    print("\nSaving model...")
    model.save_pretrained(str(output_dir / "lora"))
    processor.save_pretrained(str(output_dir / "lora"))
    if torch.cuda.is_available():
        print(f"Peak GPU memory: {torch.cuda.max_memory_allocated() / 1024**2:.0f} MB")

    print(f"\nTraining complete! Model saved to: {output_dir}")

    # Measured after the adapter is on disk, so a failed measurement never
    # costs the trained adapter.
    if eval_texts is not None and eval_callback:
        if not eval_texts:
            eval_callback({"reason": f"every held-out row is longer than max_seq_length {max_seq_length}"})
        else:
            print("\nMeasuring held-out loss (adapter, then base model)...")
            try:
                losses = heldout_losses(model, tokenizer, eval_texts, max_seq_length)
            except Exception as e:
                losses = {"reason": f"held-out evaluation failed: {type(e).__name__}: {e}"}
            print(f"Held-out: {losses}")
            eval_callback(losses)

    return str(output_dir)


def merge_adapter(base_model: str, lora_dir: str, out_dir: str) -> str:
    """Fold a LoRA adapter into its bf16 base model and save the merged
    safetensors with the tokenizer, on the CPU (no GPU claim, no network)."""
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Merging {lora_dir} into {base_model}")
    base = AutoModelForCausalLM.from_pretrained(base_model, dtype=torch.bfloat16, local_files_only=True)
    merged = PeftModel.from_pretrained(base, lora_dir).merge_and_unload()
    merged.save_pretrained(str(out), safe_serialization=True)
    AutoTokenizer.from_pretrained(lora_dir, local_files_only=True).save_pretrained(str(out))
    print(f"Merged model saved to {out}")
    return str(out)


def export_to_gguf(model_dir: str, quantization: str = "q4_k_m"):
    """Export fine-tuned model to GGUF for Ollama."""
    from unsloth import FastLanguageModel

    model_dir = Path(model_dir)
    lora_dir = model_dir / "lora"

    if not lora_dir.exists():
        print(f"LoRA model not found: {lora_dir}")
        return None

    print("\n=== Exporting to GGUF ===")
    print(f"Model: {lora_dir}")
    print(f"Quantization: {quantization}")

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=str(lora_dir),
        max_seq_length=2048,
        dtype=None,
        load_in_4bit=True,
    )

    gguf_path = model_dir / f"model-{quantization}.gguf"

    model.save_pretrained_gguf(
        str(model_dir),
        tokenizer,
        quantization_method=quantization
    )

    print(f"GGUF exported: {gguf_path}")
    return str(gguf_path)


def create_ollama_modelfile(model_dir: str, model_name: str):
    """Create Ollama Modelfile for import."""
    model_dir = Path(model_dir)

    gguf_files = list(model_dir.glob("*.gguf"))
    if not gguf_files:
        print("No GGUF file found")
        return None

    gguf_file = gguf_files[0]

    # Sampling knobs come from services.sampling_profiles (single source of
    # truth) so a fine-tuned model imported here matches what the app passes at
    # runtime. This script runs in a standalone training venv where the backend
    # package may not be importable, so fall back to the balanced-profile values
    # (kept in sync with services/sampling_profiles.py::BALANCED) if the import
    # fails. For hardware-aware Modelfiles, prefer services.modelfile_generator.
    try:
        from backend.services import sampling_profiles
        param_block = sampling_profiles.profile_modelfile_params(
            sampling_profiles.DEFAULT_PROFILE
        )
    except Exception:
        param_block = (
            "PARAMETER temperature 0.5\n"
            "PARAMETER min_p 0.05\n"
            "PARAMETER top_p 0.95\n"
            "PARAMETER top_k 40\n"
            "PARAMETER repeat_penalty 1.1"
        )

    modelfile_content = f"""FROM {gguf_file}

{param_block}

SYSTEM \"\"\"You are a helpful, accurate, and concise assistant. You are honest about what you know and don't know. When you have search results, synthesize them into direct answers - never paste raw data.\"\"\"
"""

    modelfile_path = model_dir / "Modelfile"
    modelfile_path.write_text(modelfile_content)

    print(f"\nModelfile created: {modelfile_path}")
    print("\nTo import to Ollama:")
    print(f"  cd {model_dir}")
    print(f"  ollama create {model_name} -f Modelfile")

    return str(modelfile_path)


def _die_with_parent(parent_pid):
    """Linux: have the kernel kill this process when the backend task that
    started it dies, so a killed worker never leaves a trainer on the GPU."""
    if not parent_pid or not sys.platform.startswith("linux"):
        return
    try:
        import ctypes
        import signal
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
    except Exception:
        return
    if os.getppid() != int(parent_pid):
        os._exit(1)


def _read_spec(path: str, keys: tuple) -> dict:
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    unknown = set(spec) - set(keys)
    if unknown:
        raise ValueError(f"Unknown spec keys: {sorted(unknown)}")
    return spec


def run_spec(spec: dict) -> str:
    """Train as `run --spec` asks, reporting progress, dataset and held-out
    loss as events; the last event is "done" with the output folder."""
    def progress(step, total, loss, metrics):
        emit("progress", step=step, total=total, loss=loss, metrics=metrics)

    model_dir = finetune(
        base_model=spec["base_model"],
        data_path=spec["data_path"],
        output_dir=spec["output_dir"],
        max_steps=int(spec["max_steps"]),
        learning_rate=float(spec["learning_rate"]),
        batch_size=int(spec["batch_size"]),
        lora_rank=int(spec["lora_rank"]),
        max_seq_length=int(spec["max_seq_length"]),
        gradient_accumulation_steps=int(spec.get("gradient_accumulation_steps") or 4),
        resume=bool(spec.get("resume")),
        eval_data_path=spec.get("eval_data_path"),
        eval_callback=lambda result: emit("eval", **result),
        progress_callback=progress,
        load_in_4bit=bool(spec.get("load_in_4bit")),
        response_markers=spec.get("response_markers"),
        dataset_callback=lambda report: emit("dataset", **report),
    )
    emit("done", model_dir=model_dir)
    return model_dir


def main():
    parser = argparse.ArgumentParser(description='GUAARDVARK Model Fine-Tuner')
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    train_parser = subparsers.add_parser('train', help='Fine-tune a model')
    train_parser.add_argument('--base', required=True, help='Base model (local folder or cached Hugging Face id)')
    train_parser.add_argument('--data', required=True, help='Training data (JSONL or JSON)')
    train_parser.add_argument('--name', help='Output model name')
    train_parser.add_argument('--steps', type=int, default=500, help='Training steps')
    train_parser.add_argument('--lr', type=float, default=2e-4, help='Learning rate')
    train_parser.add_argument('--batch', type=int, default=2, help='Batch size')
    train_parser.add_argument('--grad-acc', type=int, default=4, help='Gradient accumulation steps')
    train_parser.add_argument('--rank', type=int, default=16, help='LoRA rank')
    train_parser.add_argument('--seq', type=int, default=2048, help='Max sequence length (use 1024 for large models)')
    train_parser.add_argument('--no-freeze-vision', action='store_true', help='Do not freeze vision tower')
    train_parser.add_argument('--resume', action='store_true', help='Resume from last checkpoint if available')

    run_parser = subparsers.add_parser('run', help='Train from a JSON spec (the backend uses this)')
    run_parser.add_argument('--spec', required=True, help='Spec file (keys: SPEC_KEYS)')

    merge_parser = subparsers.add_parser('merge', help='Merge an adapter into its base model')
    merge_parser.add_argument('--spec', required=True, help='Spec file (keys: MERGE_SPEC_KEYS)')

    export_parser = subparsers.add_parser('export', help='Export to GGUF')
    export_parser.add_argument('--model', required=True, help='Model directory')
    export_parser.add_argument('--quant', default='q4_k_m', help='Quantization method')

    ollama_parser = subparsers.add_parser('ollama', help='Create Ollama Modelfile')
    ollama_parser.add_argument('--model', required=True, help='Model directory')
    ollama_parser.add_argument('--name', required=True, help='Ollama model name')

    check_parser = subparsers.add_parser('check', help='Check dependencies')
    check_parser.add_argument('--json', action='store_true', help='Print one JSON report')

    args = parser.parse_args()

    if args.command in ('train', 'run'):
        # Before torch is imported, so the allocator reads it.
        os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    if args.command == 'check':
        if args.json:
            report = check_report()
            print(json.dumps(report))
            sys.exit(0 if report["ok"] else 1)
        check_dependencies()

    elif args.command == 'run':
        spec = _read_spec(args.spec, SPEC_KEYS)
        _die_with_parent(spec.get("parent_pid"))
        run_spec(spec)

    elif args.command == 'merge':
        spec = _read_spec(args.spec, MERGE_SPEC_KEYS)
        _die_with_parent(spec.get("parent_pid"))
        out = merge_adapter(spec["base_model"], spec["lora_dir"], spec["out_dir"])
        emit("done", model_dir=out)

    elif args.command == 'train':
        if not check_dependencies():
            return
        finetune(
            base_model=args.base,
            data_path=args.data,
            output_name=args.name,
            max_steps=args.steps,
            learning_rate=args.lr,
            batch_size=args.batch,
            lora_rank=args.rank,
            max_seq_length=args.seq,
            gradient_accumulation_steps=args.grad_acc,
            freeze_vision=not args.no_freeze_vision,
            resume=args.resume
        )

    elif args.command == 'export':
        export_to_gguf(args.model, args.quant)

    elif args.command == 'ollama':
        create_ollama_modelfile(args.model, args.name)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
