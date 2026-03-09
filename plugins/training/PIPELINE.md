# Guaardvark Training Pipeline

## Directory Structure

```
training/
├── raw_transcripts/    # Drop your transcript files here
├── processed/          # Parsed training pairs (JSONL)
├── datasets/           # Final training datasets
├── models/             # Fine-tuned models + GGUF exports
└── scripts/
    ├── transcript_parser.py   # Parse transcripts → training pairs
    └── finetune_model.py      # Fine-tune + export to Ollama
```

## Quick Start

### Step 1: Parse Transcripts

```bash
# Single file
python3 training/scripts/transcript_parser.py /path/to/transcript.json

# Entire directory (recursive)
python3 training/scripts/transcript_parser.py /path/to/transcripts/ -r

# With Alpaca format output
python3 training/scripts/transcript_parser.py /path/to/transcripts/ -r --alpaca
```

### Step 2: Install Fine-tuning Dependencies

```bash
pip install unsloth datasets trl transformers
```

### Step 3: Fine-tune Model

```bash
# Check dependencies
python3 training/scripts/finetune_model.py check

# Fine-tune (example with gemma-2-2b)
python3 training/scripts/finetune_model.py train \
    --base unsloth/gemma-2-2b \
    --data training/processed/training_corpus_XXXXX.jsonl \
    --name guaardvark-gemma2 \
    --steps 500
```

### Step 4: Export to GGUF

```bash
python3 training/scripts/finetune_model.py export \
    --model training/models/guaardvark-gemma2 \
    --quant q4_k_m
```

### Step 5: Import to Ollama

```bash
python3 training/scripts/finetune_model.py ollama \
    --model training/models/guaardvark-gemma2 \
    --name guaardvark-gemma2

cd training/models/guaardvark-gemma2
ollama create guaardvark-gemma2 -f Modelfile
```

### Step 6: Test

```bash
ollama run guaardvark-gemma2
```

## Supported Transcript Formats

- `.jsonl` - Claude Code transcripts, line-delimited JSON
- `.json` - ChatGPT exports, generic message arrays
- `.txt` / `.md` - Plain text with "Human:" / "Assistant:" patterns

## Training Tips

- **More data = better results** - Aim for 500+ examples minimum
- **Quality over quantity** - Remove bad examples (hallucinations, errors)
- **Consistent style** - Your responses should reflect how you want the model to respond
- **Iterate** - Fine-tune, test, identify failures, add examples, repeat
