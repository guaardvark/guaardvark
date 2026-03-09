# GPU Embedding Plugin

GPU-accelerated embedding generation service for Guaardvark.

## Overview

This plugin provides fast embedding generation using GPU acceleration. It runs as a standalone Flask service that can be used by the main Guaardvark application for document indexing.

## Features

- GPU-accelerated embedding generation (10-50x faster than CPU)
- Batch processing support for efficient bulk operations
- Automatic fallback to CPU/Ollama if GPU unavailable
- Health monitoring and status endpoints
- Independent lifecycle (can run separately from Guaardvark)

## Requirements

- GPU with CUDA support
- Ollama running with embedding models (nomic-embed-text, qwen3-embedding, etc.)
- Python 3.8+
- Flask
- LlamaIndex

## Installation

The plugin is automatically discovered when placed in the `/plugins/` directory. No additional installation steps required.

## Configuration

Edit `plugin.json` to configure the service:

```json
{
  "config": {
    "enabled": false,
    "service_url": "http://localhost:5002",
    "timeout": 30,
    "model": "embeddinggemma:latest",
    "use_system_model": true
  }
}
```

## Usage

### Starting the Service

```bash
# Manual start
./plugins/gpu_embedding/scripts/start.sh

# Or via generic plugin script
./scripts/plugins/start_plugin.sh gpu_embedding
```

### Stopping the Service

```bash
./plugins/gpu_embedding/scripts/stop.sh
```

### Health Check

```bash
./plugins/gpu_embedding/scripts/check.sh
# Or
curl http://localhost:5002/health
```

### Via UI

1. Go to Settings → Manage Plugins
2. Find "GPU Embedding Service"
3. Toggle "Enabled" to enable the plugin
4. Click "Start" to start the service

## API Endpoints

- `GET /health` - Service health check
- `POST /embed` - Generate embedding for single text
- `POST /embed_batch` - Generate embeddings for multiple texts
- `GET /models` - List available models

See the main plan document for detailed API specification.

## Development

### Running Tests

```bash
cd plugins/gpu_embedding
python -m pytest tests/
```

### Manual Testing

```bash
# Start service
python -m plugins.gpu_embedding.service.app

# In another terminal, test embedding
curl -X POST http://localhost:5002/embed \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world"}'
```

## Troubleshooting

### Service won't start
- Check that port 5002 is not in use
- Verify Ollama is running: `curl http://localhost:11434/api/tags`
- Check logs: `tail -f logs/gpu_embedding_service.log`

### GPU not detected
- Verify nvidia-smi works: `nvidia-smi`
- Check CUDA_VISIBLE_DEVICES environment variable
- Service will fall back to CPU mode if GPU unavailable

### Model not loading
- Ensure Ollama has the embedding model installed
- Check Ollama base URL in config
- Verify model name matches available Ollama models

## License

Part of Guaardvark project.
