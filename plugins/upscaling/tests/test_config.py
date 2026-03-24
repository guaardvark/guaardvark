import json
import pytest
from service.config import UpscalingConfig, load_config


def test_load_config_from_plugin_json(tmp_path):
    """Config loads values from plugin.json config section."""
    manifest = {
        "id": "upscaling",
        "config": {
            "default_model": "RealESRGAN_x2plus",
            "precision": "fp16",
            "compile_model": False,
            "max_tile_size": "auto",
            "batch_size": "auto",
            "job_timeout_minutes": 60,
            "target_width": 7680,
        }
    }
    plugin_json = tmp_path / "plugin.json"
    plugin_json.write_text(json.dumps(manifest))

    config = load_config(str(tmp_path))
    assert config.default_model == "RealESRGAN_x2plus"
    assert config.precision == "fp16"
    assert config.compile_model is False
    assert config.target_width == 7680
    assert config.job_timeout_minutes == 60


def test_load_config_defaults(tmp_path):
    """Config uses defaults when plugin.json has no config section."""
    manifest = {"id": "upscaling"}
    plugin_json = tmp_path / "plugin.json"
    plugin_json.write_text(json.dumps(manifest))

    config = load_config(str(tmp_path))
    assert config.default_model == "RealESRGAN_x4plus"
    assert config.precision == "bf16"
    assert config.compile_model is True
    assert config.target_width == 3840
    assert config.job_timeout_minutes == 30


def test_load_config_missing_file(tmp_path):
    """Config uses all defaults when plugin.json is missing."""
    config = load_config(str(tmp_path))
    assert config.default_model == "RealESRGAN_x4plus"
    assert config.port == 8202
