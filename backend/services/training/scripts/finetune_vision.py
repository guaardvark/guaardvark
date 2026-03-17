#!/usr/bin/env python3

import os
import json
import argparse
from pathlib import Path
from datetime import datetime
import torch

TRAINING_DIR = Path(os.environ.get('GUAARDVARK_ROOT', '.')) / "training"
DATASETS_DIR = TRAINING_DIR / "datasets"
MODELS_DIR = TRAINING_DIR / "models"

if "PYTORCH_CUDA_ALLOC_CONF" not in os.environ:
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"


def check_dependencies():
    missing = []
    try:
        import unsloth
        from unsloth import FastVisionModel
    except ImportError:
        missing.append("unsloth (FastVisionModel)")

    if missing:
        print(f"\nMissing packages: {missing}")
        return False
    return True


def finetune(
    base_model: str,
    data_path: str,
    image_folder: str,
    output_name: str = None,
    max_steps: int = 500,
    learning_rate: float = 2e-4,
    batch_size: int = 2,
    lora_rank: int = 16,
    max_seq_length: int = 2048,
    gradient_accumulation_steps: int = 4,
    offload_to_cpu: bool = False,
    progress_callback: callable = None
):

    from unsloth import FastVisionModel, is_bf16_supported
    from trl import SFTTrainer
    from transformers import TrainingArguments

    output_name = output_name or f"guaardvark-vision-{base_model.replace('/', '-')}"
    output_dir = MODELS_DIR / output_name
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== GUAARDVARK Vision Fine-Tuning ===")
    print(f"Base model: {base_model}")
    print(f"Data: {data_path}")
    print(f"Images: {image_folder}")
    
    model, tokenizer = FastVisionModel.from_pretrained(
        model_name = base_model,
        load_in_4bit = True,
        use_gradient_checkpointing = "unsloth",
    )

    model = FastVisionModel.get_peft_model(
        model,
        finetune_vision_layers = True, 
        finetune_language_layers = True,
        finetune_attention_modules = True,
        finetune_mlp_modules = True,
        r = lora_rank,
        lora_alpha = lora_rank,
        lora_dropout = 0,
        bias = "none",
        random_state = 42,
        use_rslora = False,
        loftq_config = None,
    )

    from datasets import Dataset, Image
    
    def load_and_format_data():
        records = []
        with open(data_path) as f:
            for line in f:
                item = json.loads(line)
                if "image" in item:
                    img_path = Path(image_folder) / item["image"]
                    item["image"] = str(img_path)
                records.append(item)
        
        ds = Dataset.from_list(records)
        ds = ds.cast_column("image", Image())
        return ds

    dataset = load_and_format_data()
    
    def format_vision_data(examples):
        conversations = examples["conversations"]
        images = examples["image"]
        
        texts = []
        for conversation in conversations:
             text = tokenizer.apply_chat_template(conversation, tokenize=False, add_generation_prompt=False)
             texts.append(text)
             
        return {"text": texts, "image": images}

    
    print(f"Training examples: {len(dataset)}")

    training_args = TrainingArguments(
        per_device_train_batch_size = batch_size,
        gradient_accumulation_steps = gradient_accumulation_steps,
        warmup_steps = 10,
        max_steps = max_steps,
        learning_rate = learning_rate,
        fp16 = not is_bf16_supported(),
        bf16 = is_bf16_supported(),
        logging_steps = 10,
        optim = "adamw_8bit",
        weight_decay = 0.01,
        lr_scheduler_type = "linear",
        seed = 42,
        output_dir = str(output_dir / "checkpoints"),
        save_steps = 100,
        remove_unused_columns = False,
    )

    trainer = SFTTrainer(
        model = model,
        tokenizer = tokenizer,
        train_dataset = dataset,
        dataset_text_field = "text",
        max_seq_length = max_seq_length,
        dataset_num_proc = 2,
        args = training_args,
    )

    import gc
    gc.collect()
    torch.cuda.empty_cache()

    print("\nStarting training...")
    trainer.train()

    print("\nSaving model...")
    model.save_pretrained(str(output_dir / "lora"))
    tokenizer.save_pretrained(str(output_dir / "lora"))

    return str(output_dir)

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')
    
    train_parser = subparsers.add_parser('train')
    train_parser.add_argument('--base', required=True)
    train_parser.add_argument('--data', required=True)
    train_parser.add_argument('--images', required=True, help="Folder containing images")
    train_parser.add_argument('--name')
    train_parser.add_argument('--steps', type=int, default=500)
    train_parser.add_argument('--batch', type=int, default=2)
    
    args = parser.parse_args()
    
    if args.command == 'train':
        finetune(args.base, args.data, args.images, args.name, max_steps=args.steps, batch_size=args.batch)

if __name__ == "__main__":
    main()
