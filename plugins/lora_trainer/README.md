# LoRA Trainer Plugin

Trains character, environment, and prop LoRAs for the Film Crew.

## v1 (Mock-First)

This v1 release is **mock-only**. It provides the Celery wiring, observability, and UX integration so the casting flow works end-to-end without requiring a GPU. The `mock_trainer.py` fakes the outputs (a safetensors file and JSON metadata sidecar) in <2s so the flow is testable on any machine.

## v1.1 (Real Training)

v1.1 will land the real Diffusers/PEFT training implementation in an isolated torch venv (`venv-torch/`), driven via subprocess (similar to `ACEStepBackend` in audio_foundry).

### Setup for v1.1

1. Create the isolated venv: `python3 -m venv venv-torch`
2. Install dependencies: `./venv-torch/bin/pip install -r requirements-torch.txt`
3. The real implementation will plug into `backend/tasks/lora_trainer_tasks.py` by replacing the `_train_impl` indirection point to call the subprocess instead of the mock trainer.
