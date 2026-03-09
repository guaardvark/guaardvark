#!/bin/bash
echo "Exporting Gemma 3 4B..."
python3 training/scripts/finetune_model.py export --model training/models/guaardvark-gemma3-4b --quant q4_k_m

echo "Exporting Llama 3.1 8B..."
python3 training/scripts/finetune_model.py export --model training/models/guaardvark-llama3.1-8b --quant q4_k_m
