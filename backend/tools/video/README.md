# Video Model Download Tools

This directory contains tools for downloading video generation models.

## CogVideoX Models

### Download Script

Use `download_cogvideox_models.py` to download CogVideoX models to `data/models/video_diffusion/`.

#### Usage

```bash
# From the project root
python backend/tools/video/download_cogvideox_models.py

# Or make it executable and run directly
chmod +x backend/tools/video/download_cogvideox_models.py
./backend/tools/video/download_cogvideox_models.py
```

#### Available Models

The script will download all three CogVideoX models:

1. **cogvideox-2b** (`THUDM/CogVideoX-2b`)
   - Text-to-video generation
   - 6 second videos at 720x480
   - Requires ~12GB VRAM (with CPU offload)
   - Fastest option

2. **cogvideox-5b** (`THUDM/CogVideoX-5b`)
   - Text-to-video generation
   - 6 second videos at 720x480
   - Requires ~16GB VRAM (with CPU offload)
   - Best quality

3. **cogvideox-5b-i2v** (`THUDM/CogVideoX-5b-I2V`)
   - Image-to-video generation
   - 6 second videos at 720x480
   - Requires ~16GB VRAM (with CPU offload)
   - Converts images to video

#### Requirements

- Python 3.8+
- PyTorch installed
- diffusers library with CogVideoX support:
  ```bash
  pip install diffusers --upgrade
  ```
- Stable internet connection
- Sufficient disk space (each model is several GB)

#### How It Works

The script:
1. Checks if models already exist in `data/models/video_diffusion/`
2. Downloads missing models from HuggingFace
3. Stores models in the HuggingFace cache format
4. Models are automatically used by the video generation service

#### Notes

- Models are downloaded on first use if not already present
- The script skips models that are already downloaded
- Download progress is shown in the console
- Models are cached locally, so subsequent runs are faster

