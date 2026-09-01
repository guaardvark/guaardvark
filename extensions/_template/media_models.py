"""Optional: video models and families this extension brings.

Called once at startup after the extension's models are imported. Add a
registry entry the way backend/services/video_model_registry.py declares
them (files[].dst is the source of truth; declare the capability keys so the
Video Generator, the chat tool and Film Crew read them), and a family spec
when the extension also ships a workflow builder for a new family.
"""
from backend.services.video_model_registry import register_family_spec, register_video_model  # noqa: F401


def register() -> None:
    # register_family_spec("example", {"dimension_alignment": 16, "max_pixel_area": 1_000_000,
    #                                  "min_vram_gb": 16, "frame_rule": "4n+1", "lora_slot": None,
    #                                  "audio_out": False, "guidance": 6.0})
    # register_video_model("example-t2v", {...})
    return None
