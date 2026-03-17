"""Shared test fixtures for Discord bot tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_interaction():
    """Create a mock Discord interaction for testing slash commands."""
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = 123456789
    interaction.user.name = "testuser"
    interaction.user.display_name = "Test User"
    interaction.guild = MagicMock()
    interaction.guild.id = 987654321
    interaction.channel = MagicMock()
    interaction.channel.id = 111222333
    return interaction


@pytest.fixture
def mock_api_client():
    """Create a mock GuaardvarkClient for testing commands without real API calls."""
    client = AsyncMock()
    client.health_check = AsyncMock(return_value={"status": "ok"})
    client.chat = AsyncMock(
        return_value={
            "response": "Hello! I'm Guaardvark.",
            "session_id": "discord_123456789",
            "model_used": "llama3",
            "response_time": 1.2,
        }
    )
    client.generate_image = AsyncMock(
        return_value={
            "batch_id": "test-batch-123",
            "message": "Batch generation started",
            "prompt_count": 1,
        }
    )
    client.get_batch_status = AsyncMock(
        return_value={
            "status": "completed",
            "total_images": 1,
            "completed_images": 1,
            "results": [{"success": True, "image_path": "/tmp/images/img_001.png"}],
        }
    )
    client.get_batch_image = AsyncMock(return_value=b"\x89PNG\r\n" + b"\x00" * 100)
    client.enhance_prompt = AsyncMock(
        return_value={
            "enhanced_prompt": "A beautiful landscape, detailed, 8k",
            "negative_prompt": "blurry, low quality",
        }
    )
    client.semantic_search = AsyncMock(
        return_value={
            "answer": "The answer is 42.",
            "sources": [{"content": "source text", "metadata": {}}],
        }
    )
    client.generate_csv = AsyncMock(
        return_value={
            "message": "Generation complete",
            "output_file": "test_output.csv",
            "statistics": {"generated_items": 10, "processing_time": 5.0},
        }
    )
    client.get_diagnostics = AsyncMock(
        return_value={
            "active_model": "llama3",
            "ollama_reachable": True,
            "model_count": 3,
            "document_count": 150,
            "version": "2.4.1",
            "platform": "linux",
        }
    )
    client.get_detailed_diagnostics = AsyncMock(
        return_value={
            "cpu_percent": 45.0,
            "memory_percent": 62.0,
            "llm_ready": True,
        }
    )
    client.get_models = AsyncMock(
        return_value={
            "models": [
                {
                    "name": "llama3",
                    "details": {"parameter_size": "8B", "quantization_level": "Q4"},
                },
                {
                    "name": "mistral",
                    "details": {"parameter_size": "7B", "quantization_level": "Q4"},
                },
            ]
        }
    )
    client.switch_model = AsyncMock(
        return_value={
            "message": "Model switch to llama3 started",
            "status": "switching",
        }
    )
    client.speech_to_text = AsyncMock(
        return_value={
            "text": "Hello Guaardvark",
            "language": "en",
            "duration": 2.5,
        }
    )
    client.text_to_speech = AsyncMock(
        return_value={
            "audio_url": "/api/voice/audio/tts_test.wav",
            "filename": "tts_test.wav",
        }
    )
    client.get_voice_audio = AsyncMock(return_value=b"\x00" * 1000)
    return client


@pytest.fixture
def sample_config():
    """Return a test configuration dict."""
    return {
        "bot": {"token": "test-token", "guild_id": None},
        "api": {
            "base_url": "http://localhost:5002/api",
            "timeout": 120,
            "health_check_interval": 60,
        },
        "security": {
            "admin_roles": ["Admin"],
            "allowed_channels": [],
            "allow_dms": True,
            "max_prompt_length": 2000,
            "max_image_prompt_length": 500,
        },
        "rate_limits": {
            "ask": 10,
            "imagine": 3,
            "generate_csv": 2,
            "search": 15,
            "enhance_prompt": 10,
        },
        "voice": {
            "enabled": True,
            "silence_threshold_ms": 1500,
            "max_listen_duration_s": 30,
            "tts_voice": "ryan",
            "interrupt_on_speech": True,
        },
        "image": {"max_queue_depth": 5, "default_steps": 20, "default_size": 512},
        "conversation": {"max_history": 50},
    }
