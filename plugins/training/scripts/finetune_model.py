#!/usr/bin/env python3

import os
import json
import argparse
from pathlib import Path
from datetime import datetime

TRAINING_DIR = Path(os.environ.get('GUAARDVARK_ROOT', '.')) / "training"
DATASETS_DIR = TRAINING_DIR / "datasets"
MODELS_DIR = TRAINING_DIR / "models"

if "PYTORCH_CUDA_ALLOC_CONF" not in os.environ:
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"


def check_dependencies():
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


def format_prompt(example):
    instruction = example.get('instruction', '')
    inp = example.get('input', '')
    output = example.get('output', '')

    if inp:
        text = f"""### Instruction:
{instruction}

{inp}

{output}"""
    else:
        text = f"""### Instruction:
{instruction}

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
    progress_callback: callable = None
):
    from unsloth import FastLanguageModel
    from trl import SFTTrainer
    from transformers import TrainingArguments

    output_name = output_name or f"guaardvark-{base_model.replace('/', '-').replace(':', '-')}"
    output_dir = MODELS_DIR / output_name
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Guaardvark Fine-Tuning ===")
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

    import torch
    import gc
    if torch.cuda.is_available():
        print(f"GPU Memory after loading model: {torch.cuda.memory_allocated() / 1024**3:.2f} GB")

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
        else:
            print("No vision tower found to freeze (or path incorrect).")

    dataset = load_training_data(data_path)
    dataset = dataset.map(format_prompt)

    original_len = len(dataset)

    def filter_long_examples(example):
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
        import torch
        import torch.distributed as dist

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

    print("\nClearing GPU cache before training...")
    gc.collect()
    torch.cuda.empty_cache()
    if torch.cuda.is_available():
        print(f"GPU Memory before train: {torch.cuda.memory_allocated() / 1024**3:.2f} GB")

    print("\nStarting training...")
    trainer.train()

    print("\nSaving model...")
    model.save_pretrained(str(output_dir / "lora"))
    tokenizer.save_pretrained(str(output_dir / "lora"))

    print(f"\nTraining complete! Model saved to: {output_dir}")

    return str(output_dir)


def export_to_gguf(model_dir: str, quantization: str = "q4_k_m"):
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
