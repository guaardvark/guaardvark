"""Video decode/encode pipeline using ffmpeg with NVDEC/NVENC.

Handles frame reading, writing, audio passthrough, and GPU-accelerated I/O.
"""
import logging
import os
import subprocess
from typing import Any, Callable, Dict, Optional

import cv2
import ffmpeg
import numpy as np

logger = logging.getLogger("upscaling.video_pipeline")


def get_video_info(video_path: str) -> Dict[str, Any]:
    """Get video metadata via ffprobe."""
    probe = ffmpeg.probe(video_path)
    video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    has_audio = any(s["codec_type"] == "audio" for s in probe["streams"])

    vs = video_streams[0]
    fps_parts = vs["avg_frame_rate"].split("/")
    fps = float(fps_parts[0]) / float(fps_parts[1]) if len(fps_parts) == 2 else float(fps_parts[0])

    return {
        "width": int(vs["width"]),
        "height": int(vs["height"]),
        "fps": fps,
        "nb_frames": int(vs.get("nb_frames", 0)),
        "has_audio": has_audio,
    }


def _check_nvenc_available() -> bool:
    """Check if NVENC h264 encoder is available."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
        )
        return "h264_nvenc" in result.stdout
    except Exception:
        return False


def _check_nvdec_available() -> bool:
    """Check if CUDA hardware decoding is available."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-hwaccels"],
            capture_output=True, text=True, timeout=5,
        )
        return "cuda" in result.stdout
    except Exception:
        return False


def process_video(
    input_path: str,
    output_path: str,
    frame_processor: Callable[[np.ndarray], np.ndarray],
    out_width: int,
    out_height: int,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> None:
    """Process a video frame-by-frame with the given processor function.

    Args:
        input_path: Path to input video.
        output_path: Path for output video.
        frame_processor: Function that takes HWC uint8 BGR frame and returns upscaled frame.
        out_width: Output video width.
        out_height: Output video height.
        progress_callback: Called with frame count after each frame.
    """
    info = get_video_info(input_path)
    width, height = info["width"], info["height"]
    fps = info["fps"]
    has_audio = info["has_audio"]

    use_nvdec = _check_nvdec_available()
    use_nvenc = _check_nvenc_available()

    # --- Build reader process (Fix 1: conditionally add hwaccel) ---
    reader_kwargs = {}
    if use_nvdec:
        reader_kwargs["hwaccel"] = "cuda"

    reader_process = (
        ffmpeg.input(input_path, **reader_kwargs)
        .output("pipe:", format="rawvideo", pix_fmt="bgr24", loglevel="error")
        .run_async(pipe_stdout=True)
    )

    # --- Build writer process ---
    tmp_output = output_path + ".tmp.mp4"
    vcodec = "h264_nvenc" if use_nvenc else "libx264"
    writer_args = {
        "pix_fmt": "yuv420p",
        "vcodec": vcodec,
        "loglevel": "error",
    }
    if vcodec == "h264_nvenc":
        writer_args["preset"] = "p4"
        writer_args["rc"] = "vbr"
        writer_args["cq"] = "18"
    else:
        writer_args["crf"] = "18"
        writer_args["preset"] = "slow"

    writer_process = (
        ffmpeg.input(
            "pipe:", format="rawvideo", pix_fmt="bgr24",
            s=f"{out_width}x{out_height}", framerate=fps,
        )
        .output(tmp_output, **writer_args)
        .overwrite_output()
        .run_async(pipe_stdin=True)
    )

    # --- Process frames ---
    frame_count = 0
    frame_size = width * height * 3

    while True:
        raw = reader_process.stdout.read(frame_size)
        if not raw or len(raw) < frame_size:
            break

        frame = np.frombuffer(raw, np.uint8).reshape(height, width, 3)
        processed = frame_processor(frame)
        writer_process.stdin.write(processed.tobytes())

        frame_count += 1
        if progress_callback:
            progress_callback(frame_count)

    reader_process.stdout.close()
    reader_process.wait()
    writer_process.stdin.close()
    writer_process.wait()

    # --- Mux audio from source if present ---
    if has_audio:
        final_tmp = output_path + ".mux.mp4"
        mux_cmd = [
            "ffmpeg", "-y",
            "-i", tmp_output,
            "-i", input_path,
            "-c:v", "copy",
            "-c:a", "copy",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            final_tmp,
        ]
        subprocess.run(mux_cmd, capture_output=True, check=True)
        os.replace(final_tmp, output_path)
        os.remove(tmp_output)
    else:
        os.replace(tmp_output, output_path)

    logger.info(f"Video processing complete: {frame_count} frames -> {output_path}")
