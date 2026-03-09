#!/usr/bin/env python3
"""
GUAARDVARK Model Fine-Tuner
Uses unsloth for efficient LoRA fine-tuning on consumer GPUs.
Exports to GGUF for Ollama import.
"""

import os
import json
import argparse
from pathlib import Path
from datetime import datetime

TRAINING_DIR = Path("/home/llamax1/LLAMAX5.2/training")
DATASETS_DIR = TRAINING_DIR / "datasets"
MODELS_DIR = TRAINING_DIR / "models"

# Set CUDA allocator config to reduce fragmentation
if "PYTORCH_CUDA_ALLOC_CONF" not in os.environ:
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"


def check_dependencies():
    """Check if required packages are installed."""
    missing = []

    try:
        import torch
        print(f"PyTorch: {torch.__version__}, CUDA: {torch.cuda.is_available()}")
    except ImportError:
        missing.append("torch")

    try:
        import unsloth
        print(f"Unsloth: installed")
    except ImportError:
        missing.append("unsloth")

    try:
        from datasets import load_dataset
        print(f"Datasets: installed")
    except ImportError:
        missing.append("datasets")

    try:
        from trl import SFTTrainer
        print(f"TRL: installed")
    except ImportError:
        missing.append("trl")

    if missing:
        print(f"\nMissing packages: {missing}")
        print("Install with: pip install unsloth datasets trl")
        return False

    return True


def load_training_data(data_path: str):
    """Load training data from JSONL or Alpaca JSON."""
    from datasets import Dataset

    data_path = Path(data_path)

    if data_path.suffix == '.jsonl':
        records = []
        with open(data_path) as f:
            for line in f:
                records.append(json.loads(line))
        return Dataset.from_list(records)

    elif data_path.suffix == '.json':
        with open(data_path) as f:
            records = json.load(f)
        return Dataset.from_list(records)

    else:
        raise ValueError(f"Unsupported format: {data_path.suffix}")


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


def format_prompt(example):
    """Format training example into prompt."""
    instruction = example.get('instruction', '')
    inp = example.get('input', '')
    output = example.get('output', '')

    if inp:
        text = f"""### Instruction:
{instruction}

### Input:
{inp}

### Response:
{output}"""
    else:
        text = f"""### Instruction:
{instruction}

### Response:
{output}"""

    return {"text": text}


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
    offload_to_cpu: bool = True,
    freeze_vision: bool = True,
    progress_callback: callable = None,
    resume: bool = False
):
    """
    Fine-tune a model with LoRA.

    Args:
        base_model: Base model to fine-tune (HuggingFace ID or local path)
        data_path: Path to training data (JSONL or JSON)
        output_name: Name for output model
        max_steps: Maximum training steps
        learning_rate: Learning rate
        batch_size: Per-device batch size
        lora_rank: LoRA adapter rank
        max_seq_length: Maximum sequence length
        gradient_accumulation_steps: Gradient accumulation steps
        offload_to_cpu: Offload optimizer states to CPU
        freeze_vision: Freeze vision tower (for multimodal models)
        progress_callback: Callback for progress updates
        resume: If True, resume from last checkpoint if available
    """

    from unsloth import FastLanguageModel
    from trl import SFTTrainer
    from transformers import TrainingArguments

    output_name = output_name or f"guaardvark-{base_model.replace('/', '-').replace(':', '-')}"
    output_dir = MODELS_DIR / output_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check for existing checkpoint if resuming
    resume_checkpoint = None
    if resume:
        resume_checkpoint = find_last_checkpoint(output_dir)
        if resume_checkpoint:
            print(f"\n=== RESUMING FROM CHECKPOINT ===")
            print(f"Checkpoint: {resume_checkpoint}")
        else:
            print("\nNo checkpoint found, starting fresh training.")

    print(f"\n=== GUAARDVARK Fine-Tuning ===")
    print(f"Base model: {base_model}")
    print(f"Training data: {data_path}")
    print(f"Output: {output_dir}")
    print(f"Max steps: {max_steps}")
    print(f"Max seq length: {max_seq_length}")
    print(f"Batch size: {batch_size}")
    print(f"Gradient accumulation steps: {gradient_accumulation_steps}")
    print(f"LoRA rank: {lora_rank}")
    print(f"CPU offload: {offload_to_cpu}")
    print(f"Freeze Vision Tower: {freeze_vision}")

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=base_model,
        max_seq_length=max_seq_length,
        dtype=None,
        load_in_4bit=True,
    )

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

    # Check memory after model load
    import torch
    import gc
    if torch.cuda.is_available():
        print(f"GPU Memory after loading model: {torch.cuda.memory_allocated() / 1024**3:.2f} GB")

    # Freeze vision tower if requested
    if freeze_vision:
        # Try different paths to find vision tower
        vision_tower = None
        if hasattr(model, "model") and hasattr(model.model, "vision_tower"):
             vision_tower = model.model.vision_tower
        elif hasattr(model, "base_model") and hasattr(model.base_model, "model") and hasattr(model.base_model.model, "vision_tower"):
             vision_tower = model.base_model.model.vision_tower
        
        if vision_tower:
            print("Freezing vision tower to save memory...")
            vision_tower.requires_grad_(False)
            # Ensure it is in eval mode too (disable dropout etc)
            vision_tower.eval()
            for param in vision_tower.parameters():
                param.requires_grad = False
        else:
            print("No vision tower found to freeze (or path incorrect).")

    dataset = load_training_data(data_path)
    dataset = dataset.map(format_prompt)

    # Filter out examples that are too long to prevent OOM/CUDA errors
    original_len = len(dataset)
    
    def filter_long_examples(example):
        # Use the tokenizer to get the exact token count
        # We process single example, so we get a list of IDs
        ids = tokenizer(example["text"], truncation=False, add_special_tokens=True)["input_ids"]
        return len(ids) <= max_seq_length

    print("Filtering dataset based on token length...")
    dataset = dataset.filter(filter_long_examples)
    filtered_len = len(dataset)
    
    if filtered_len < original_len:
        print(f"Filtered out {original_len - filtered_len} examples exceeding max sequence length of {max_seq_length}.")
    
    if filtered_len == 0:
        raise ValueError("All training examples were filtered out! Increase max_seq_length or check your data.")

    print(f"Training examples: {len(dataset)}")

    training_args = {
        "per_device_train_batch_size": batch_size,
        "gradient_accumulation_steps": gradient_accumulation_steps,
        "warmup_steps": 10,
        "max_steps": max_steps,
        "learning_rate": learning_rate,
        "bf16": True,
        "logging_steps": 10,
        "output_dir": str(output_dir / "checkpoints"),
        "optim": "paged_adamw_8bit",
        "save_steps": 100,
    }

    if offload_to_cpu:
        print("Enabling CPU offload for optimizer states...")
        # Ensure process group is initialized for DeepSpeed
        import torch
        import torch.distributed as dist
        
        # Set default env vars for single-GPU DeepSpeed if not set
        if "MASTER_ADDR" not in os.environ:
            os.environ["MASTER_ADDR"] = "127.0.0.1"
        if "MASTER_PORT" not in os.environ:
            os.environ["MASTER_PORT"] = "29500"
        if "WORLD_SIZE" not in os.environ:
            os.environ["WORLD_SIZE"] = "1"
        if "RANK" not in os.environ:
            os.environ["RANK"] = "0"
        
        try:
            if not dist.is_initialized():
                print("Initializing process group for DeepSpeed...")
                dist.init_process_group(backend="nccl")
                print("Process group initialized.")
            else:
                print("Process group already initialized.")
        except Exception as e:
            print(f"Warning: Failed to init process group: {e}")

        deepspeed_config = {
            "zero_optimization": {
                "stage": 2,
                "offload_optimizer": {
                    "device": "cpu",
                    "pin_memory": True
                },
                "allgather_partitions": True,
                "allgather_bucket_size": 2e8,
                "reduce_scatter": True,
                "reduce_bucket_size": 2e8,
                "overlap_comm": True,
            },
            "bf16": {"enabled": True},
            "train_batch_size": "auto",
            "train_micro_batch_size_per_gpu": "auto",
            "gradient_accumulation_steps": "auto",
        }
        ds_config_path = output_dir / "ds_config.json"
        with open(ds_config_path, 'w') as f:
            json.dump(deepspeed_config, f, indent=2)
        training_args["deepspeed"] = str(ds_config_path)
        training_args["optim"] = "adamw_torch"

    # Add progress callback if provided
    callbacks = []
    if progress_callback:
        from transformers import TrainerCallback
        
        class ProgressCallback(TrainerCallback):
            def __init__(self, callback_func, total_steps):
                self.callback_func = callback_func
                self.total_steps = total_steps
            
            def on_log(self, args, state, control, logs=None, **kwargs):
                if state.global_step and self.callback_func:
                    progress = int((state.global_step / self.total_steps) * 100)
                    metrics = {
                        "loss": logs.get("loss", 0),
                        "learning_rate": logs.get("learning_rate", 0),
                        "epoch": state.epoch if hasattr(state, "epoch") else 0
                    }
                    self.callback_func(state.global_step, self.total_steps, metrics.get("loss", 0), metrics)
        
        callbacks.append(ProgressCallback(progress_callback, max_steps))
    
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=max_seq_length,
        args=TrainingArguments(**training_args),
        callbacks=callbacks if callbacks else None,
    )

    # Clear cache before training
    print("\nClearing GPU cache before training...")
    gc.collect()
    torch.cuda.empty_cache()
    if torch.cuda.is_available():
        print(f"GPU Memory before train: {torch.cuda.memory_allocated() / 1024**3:.2f} GB")

    print("\nStarting training...")
    if resume_checkpoint:
        print(f"Resuming from: {resume_checkpoint}")
        trainer.train(resume_from_checkpoint=resume_checkpoint)
    else:
        trainer.train()

    print("\nSaving model...")
    model.save_pretrained(str(output_dir / "lora"))
    tokenizer.save_pretrained(str(output_dir / "lora"))

    print(f"\nTraining complete! Model saved to: {output_dir}")

    return str(output_dir)


def export_to_gguf(model_dir: str, quantization: str = "q4_k_m"):
    """Export fine-tuned model to GGUF for Ollama."""
    from unsloth import FastLanguageModel

    model_dir = Path(model_dir)
    lora_dir = model_dir / "lora"

    if not lora_dir.exists():
        print(f"LoRA model not found: {lora_dir}")
        return None

    print(f"\n=== Exporting to GGUF ===")
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

    modelfile_content = f"""FROM {gguf_file}

PARAMETER temperature 0.4
PARAMETER top_p 0.8
PARAMETER top_k 30

SYSTEM \"\"\"You are a helpful, accurate, and concise assistant. You are honest about what you know and don't know. When you have search results, synthesize them into direct answers - never paste raw data.\"\"\"
"""

    modelfile_path = model_dir / "Modelfile"
    modelfile_path.write_text(modelfile_content)

    print(f"\nModelfile created: {modelfile_path}")
    print(f"\nTo import to Ollama:")
    print(f"  cd {model_dir}")
    print(f"  ollama create {model_name} -f Modelfile")

    return str(modelfile_path)


def main():
    parser = argparse.ArgumentParser(description='GUAARDVARK Model Fine-Tuner')
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    train_parser = subparsers.add_parser('train', help='Fine-tune a model')
    train_parser.add_argument('--base', required=True, help='Base model (e.g., unsloth/gemma-2-2b)')
    train_parser.add_argument('--data', required=True, help='Training data (JSONL or JSON)')
    train_parser.add_argument('--name', help='Output model name')
    train_parser.add_argument('--steps', type=int, default=500, help='Training steps')
    train_parser.add_argument('--lr', type=float, default=2e-4, help='Learning rate')
    train_parser.add_argument('--batch', type=int, default=2, help='Batch size')
    train_parser.add_argument('--grad-acc', type=int, default=4, help='Gradient accumulation steps')
    train_parser.add_argument('--rank', type=int, default=16, help='LoRA rank')
    train_parser.add_argument('--seq', type=int, default=2048, help='Max sequence length (use 1024 for large models)')
    train_parser.add_argument('--offload', action='store_true', help='Offload optimizer to CPU (uses system RAM)')
    train_parser.add_argument('--no-freeze-vision', action='store_true', help='Do not freeze vision tower')
    train_parser.add_argument('--resume', action='store_true', help='Resume from last checkpoint if available')

    export_parser = subparsers.add_parser('export', help='Export to GGUF')
    export_parser.add_argument('--model', required=True, help='Model directory')
    export_parser.add_argument('--quant', default='q4_k_m', help='Quantization method')

    ollama_parser = subparsers.add_parser('ollama', help='Create Ollama Modelfile')
    ollama_parser.add_argument('--model', required=True, help='Model directory')
    ollama_parser.add_argument('--name', required=True, help='Ollama model name')

    check_parser = subparsers.add_parser('check', help='Check dependencies')

    args = parser.parse_args()

    if args.command == 'check':
        check_dependencies()

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
            offload_to_cpu=args.offload,
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
