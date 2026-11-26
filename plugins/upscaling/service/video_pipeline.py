"""Video decode/encode pipeline using ffmpeg with NVDEC/NVENC.

Handles frame reading, writing, audio passthrough, and GPU-accelerated I/O.

Consumer h264_nvenc caps at 4096px on either side. 4x upscale of 1080 → 4320
exceeds that limit ("Width 4320 exceeds 4096"). We probe at the *actual* output
size and prefer hevc_nvenc (or software) when h264 cannot open.
"""
import logging
import os
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple

import ffmpeg
import numpy as np

logger = logging.getLogger("upscaling.video_pipeline")

# Process-level cache: None=unprobed, True/False=result of small-size functional encode.
_nvenc_works: Optional[bool] = None
# Cache of (codec, width, height) -> bool for size-specific probes.
_encoder_size_ok: Dict[Tuple[str, int, int], bool] = {}

# Pipe can accept a few writes before a dead NVENC writer closes → frame_count > 0
# even though nothing was muxed. Restart whole encode from frame 0 in that window.
_EARLY_BROKEN_PIPE_MAX_FRAMES = 8
# Consumer GeForce h264_nvenc hard limit (Ada/Ampere); hevc_nvenc goes higher.
_H264_NVENC_MAX_SIDE = 4096


def get_video_info(video_path: str) -> Dict[str, Any]:
    """Get video metadata via ffprobe."""
    probe = ffmpeg.probe(video_path)
    video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    has_audio = any(s["codec_type"] == "audio" for s in probe["streams"])

    vs = video_streams[0]
    fps_parts = vs["avg_frame_rate"].split("/")
    fps = float(fps_parts[0]) / float(fps_parts[1]) if len(fps_parts) == 2 else float(fps_parts[0])
    pix_fmt = vs.get("pix_fmt", "yuv420p")

    return {
        "width": int(vs["width"]),
        "height": int(vs["height"]),
        "fps": fps,
        "nb_frames": int(vs.get("nb_frames", 0)),
        "has_audio": has_audio,
        "pix_fmt": pix_fmt,
    }


def _encoder_listed(name: str) -> bool:
    """True if ffmpeg lists the encoder (does not prove it can open)."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
        )
        return name in result.stdout
    except Exception:
        return False


def _check_nvenc_available() -> bool:
    """Functional NVENC probe at a safe small size (device present at all)."""
    global _nvenc_works
    if _nvenc_works is not None:
        return _nvenc_works

    if not _encoder_listed("h264_nvenc"):
        _nvenc_works = False
        return False

    ok, _ = _probe_encoder_at_size("h264_nvenc", 256, 256)
    _nvenc_works = ok
    if not ok:
        logger.warning("NVENC functional probe failed (will prefer software / hevc)")
    return ok


def _probe_encoder_at_size(vcodec: str, width: int, height: int) -> Tuple[bool, str]:
    """Return (ok, stderr_tail) for a short lavfi encode at the given size."""
    width, height = _even(width), _even(height)
    key = (vcodec, width, height)
    if key in _encoder_size_ok:
        return _encoder_size_ok[key], ""

    if "nvenc" in vcodec and not _encoder_listed(vcodec):
        _encoder_size_ok[key] = False
        return False, f"{vcodec} not listed"

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-y",
                "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:d=0.04",
                "-c:v", vcodec, "-preset", "p4" if "nvenc" in vcodec else "ultrafast",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=30,
        )
        err = (result.stderr or result.stdout or "").strip()
        ok = result.returncode == 0
        _encoder_size_ok[key] = ok
        if not ok:
            logger.info(
                "Encoder probe %s @ %sx%s failed: %s",
                vcodec, width, height, err[-300:],
            )
        return ok, err[-500:]
    except Exception as e:
        _encoder_size_ok[key] = False
        return False, str(e)


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


def _even(n: int) -> int:
    """NVENC / H.264 require even frame dimensions."""
    n = max(2, int(n))
    return n if n % 2 == 0 else n + 1


def _pick_codecs(
    use_nvenc: bool, is_10bit: bool
) -> Tuple[str, str, str]:
    """Legacy helper — prefer ``_pick_codecs_for_size`` for real encodes."""
    if use_nvenc and is_10bit:
        return "hevc_nvenc", "yuv420p10le", "libx265"
    if use_nvenc:
        return "h264_nvenc", "yuv420p", "libx264"
    if is_10bit:
        return "libx265", "yuv420p10le", "libx265"
    return "libx264", "yuv420p", "libx264"


def _pick_codecs_for_size(
    out_width: int,
    out_height: int,
    is_10bit: bool,
    force_software: bool = False,
) -> Tuple[str, str, str, str]:
    """Pick (vcodec, pix_fmt, software_fallback, reason) for the output size.

    Order: h264_nvenc (if size fits + probe OK) → hevc_nvenc → libx265/libx264.
    """
    soft = "libx265" if is_10bit else "libx264"
    soft_pix = "yuv420p10le" if is_10bit else "yuv420p"
    w, h = _even(out_width), _even(out_height)

    if force_software:
        return soft, soft_pix, soft, "forced_software"

    # Fast reject for known h264 consumer limit before spending a probe.
    h264_size_ok = w <= _H264_NVENC_MAX_SIDE and h <= _H264_NVENC_MAX_SIDE

    if h264_size_ok and _check_nvenc_available():
        ok, err = _probe_encoder_at_size("h264_nvenc", w, h)
        if ok:
            pix = soft_pix if is_10bit else "yuv420p"
            # 10-bit still prefers hevc_nvenc when available
            if is_10bit:
                ok_hevc, _ = _probe_encoder_at_size("hevc_nvenc", w, h)
                if ok_hevc:
                    return "hevc_nvenc", "yuv420p10le", soft, "10bit→hevc_nvenc"
            return "h264_nvenc", "yuv420p", soft, "h264_ok"
        logger.info("h264_nvenc rejected at %sx%s: %s", w, h, err)

    # Oversized for h264, or h264 probe failed — try hevc_nvenc (Ada supports 4320+).
    if _encoder_listed("hevc_nvenc"):
        ok, err = _probe_encoder_at_size("hevc_nvenc", w, h)
        if ok:
            pix = "yuv420p10le" if is_10bit else "yuv420p"
            reason = (
                "h264_oversize→hevc_nvenc"
                if not h264_size_ok
                else "h264_fail→hevc_nvenc"
            )
            return "hevc_nvenc", pix, soft, reason
        logger.info("hevc_nvenc rejected at %sx%s: %s", w, h, err)

    return soft, soft_pix, soft, "software"


def _build_writer(
    tmp_output: str,
    out_width: int,
    out_height: int,
    fps: float,
    vcodec: str,
    pix_fmt: str,
    double_fps: bool,
):
    writer_args = {
        "pix_fmt": pix_fmt,
        "vcodec": vcodec,
        "loglevel": "error",
    }
    if double_fps:
        writer_args["vf"] = f"minterpolate='fps={int(fps * 2)}:mi_mode=mci:mc_mode=aobmc'"

    if "nvenc" in vcodec:
        writer_args["preset"] = "p7"
        writer_args["rc"] = "vbr"
        writer_args["cq"] = "14"
    else:
        writer_args["crf"] = "14"
        writer_args["preset"] = "medium" if out_width * out_height > 8_000_000 else "veryslow"

    return (
        ffmpeg.input(
            "pipe:", format="rawvideo", pix_fmt="bgr24",
            s=f"{out_width}x{out_height}", framerate=fps,
        )
        .output(tmp_output, **writer_args)
        .overwrite_output()
        .run_async(pipe_stdin=True)
    )


class EarlyEncodePipeError(Exception):
    """Writer pipe died early; ``frames_written`` is how many frames left the upscaler."""

    def __init__(self, vcodec: str, frames_written: int, out_width: int, out_height: int, detail: str = ""):
        self.vcodec = vcodec
        self.frames_written = frames_written
        self.out_width = out_width
        self.out_height = out_height
        self.detail = detail
        super().__init__(
            f"encode BrokenPipe vcodec={vcodec} frames={frames_written} "
            f"size={out_width}x{out_height} {detail}".strip()
        )


def _drain_and_kill(proc) -> str:
    """Close pipes, wait briefly, return stderr text if any."""
    err_text = ""
    try:
        if proc is None:
            return ""
        if getattr(proc, "stdin", None):
            try:
                proc.stdin.close()
            except Exception:
                pass
        if getattr(proc, "stdout", None):
            try:
                proc.stdout.close()
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        if getattr(proc, "stderr", None):
            try:
                err_text = proc.stderr.read().decode("utf-8", errors="replace")
            except Exception:
                pass
    except Exception as e:
        err_text = str(e)
    return err_text


def _remove_partial(path: str) -> None:
    """Delete a part-written encode; a missing file is fine."""
    try:
        os.remove(path)
    except OSError:
        pass


def _open_reader(input_path: str, use_nvdec: bool):
    reader_kwargs = {}
    if use_nvdec:
        reader_kwargs["hwaccel"] = "cuda"
    return (
        ffmpeg.input(input_path, **reader_kwargs)
        .output("pipe:", format="rawvideo", pix_fmt="bgr24", loglevel="error")
        .run_async(pipe_stdout=True)
    )


def _encode_pass(
    input_path: str,
    tmp_output: str,
    frame_processor: Callable[[np.ndarray], np.ndarray],
    width: int,
    height: int,
    out_width: int,
    out_height: int,
    fps: float,
    vcodec: str,
    out_pix_fmt: str,
    use_nvdec: bool,
    double_fps: bool,
    progress_callback: Optional[Callable[[int], None]],
) -> int:
    """Run one full read→upscale→write pass. Returns frame_count.

    Raises BrokenPipeError on writer death (caller may restart from frame 0).
    """
    if os.path.exists(tmp_output):
        try:
            os.remove(tmp_output)
        except Exception:
            pass

    reader_process = _open_reader(input_path, use_nvdec)
    writer_process = _build_writer(
        tmp_output, out_width, out_height, fps, vcodec, out_pix_fmt, double_fps
    )
    frame_count = 0
    frame_size = width * height * 3

    try:
        while True:
            raw = reader_process.stdout.read(frame_size)
            if not raw or len(raw) < frame_size:
                break

            frame = np.frombuffer(raw, np.uint8).reshape(height, width, 3)
            processed = frame_processor(frame)
            ph, pw = processed.shape[:2]
            if (pw, ph) != (out_width, out_height):
                import cv2
                processed = cv2.resize(
                    processed, (out_width, out_height), interpolation=cv2.INTER_AREA
                )

            writer_process.stdin.write(processed.tobytes())
            frame_count += 1
            if progress_callback:
                progress_callback(frame_count)

        reader_process.stdout.close()
        reader_process.wait()
        try:
            writer_process.stdin.close()
        except Exception:
            pass
        writer_rc = writer_process.wait()
        if writer_rc != 0:
            raise RuntimeError(
                f"ffmpeg writer exited with code {writer_rc} ({vcodec} "
                f"@ {out_width}x{out_height})"
            )
        return frame_count
    except BrokenPipeError as e:
        raise EarlyEncodePipeError(
            vcodec=vcodec,
            frames_written=frame_count,
            out_width=out_width,
            out_height=out_height,
            detail=str(e),
        ) from e
    finally:
        _drain_and_kill(reader_process)
        _drain_and_kill(writer_process)


def process_video(
    input_path: str,
    output_path: str,
    frame_processor: Callable[[np.ndarray], np.ndarray],
    out_width: int,
    out_height: int,
    progress_callback: Optional[Callable[[int], None]] = None,
    double_fps: bool = False,
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
    orig_pix_fmt = info.get("pix_fmt", "yuv420p")

    out_width = _even(out_width)
    out_height = _even(out_height)

    use_nvdec = _check_nvdec_available()
    is_10bit = "10" in (orig_pix_fmt or "")
    tmp_output = output_path + ".tmp.mp4"

    vcodec, out_pix_fmt, soft_vcodec, reason = _pick_codecs_for_size(
        out_width, out_height, is_10bit
    )
    logger.info(
        "Video writer: vcodec=%s pix_fmt=%s size=%sx%s reason=%s",
        vcodec, out_pix_fmt, out_width, out_height, reason,
    )

    # Attempt list: primary pick, then software if we started on NVENC.
    attempts: List[Tuple[str, str, str]] = [(vcodec, out_pix_fmt, reason)]
    if "nvenc" in vcodec:
        soft_pix = "yuv420p10le" if is_10bit else "yuv420p"
        attempts.append((soft_vcodec, soft_pix, f"early_brokenpipe→{soft_vcodec}"))

    frame_count = 0
    used_fallback = False
    last_error: Optional[Exception] = None

    for attempt_i, (attempt_vcodec, attempt_pix, attempt_reason) in enumerate(attempts):
        if attempt_i > 0:
            used_fallback = True
            logger.warning(
                "Restarting encode from frame 0 with %s (%s)",
                attempt_vcodec, attempt_reason,
            )
            vcodec, out_pix_fmt, reason = attempt_vcodec, attempt_pix, attempt_reason

        try:
            frame_count = _encode_pass(
                input_path=input_path,
                tmp_output=tmp_output,
                frame_processor=frame_processor,
                width=width,
                height=height,
                out_width=out_width,
                out_height=out_height,
                fps=fps,
                vcodec=attempt_vcodec,
                out_pix_fmt=attempt_pix,
                use_nvdec=use_nvdec,
                double_fps=double_fps,
                progress_callback=progress_callback,
            )
            last_error = None
            break
        except EarlyEncodePipeError as e:
            last_error = e
            can_retry = (
                attempt_i + 1 < len(attempts)
                and e.frames_written < _EARLY_BROKEN_PIPE_MAX_FRAMES
                and "nvenc" in attempt_vcodec
            )
            logger.warning(
                "Encode BrokenPipe (%s) frames_written=%s can_retry=%s",
                e, e.frames_written, can_retry,
            )
            if not can_retry:
                _remove_partial(tmp_output)
                raise RuntimeError(
                    f"Video encode BrokenPipe with {attempt_vcodec} "
                    f"(output {out_width}x{out_height}, frames_written={e.frames_written}). "
                    f"h264_nvenc max side is {_H264_NVENC_MAX_SIDE}; "
                    f"oversize outputs need hevc_nvenc or software. Detail: {e}"
                ) from e
        except RuntimeError as e:
            last_error = e
            if (
                attempt_i + 1 < len(attempts)
                and "nvenc" in attempt_vcodec
            ):
                logger.warning("Encode RuntimeError on NVENC, retrying software: %s", e)
                continue
            _remove_partial(tmp_output)
            raise
        except BaseException:
            # The frame processor stopped the job (a bad frame, a cancel): the
            # part-written file must not stay beside finished results.
            _remove_partial(tmp_output)
            raise

    if last_error is not None:
        raise RuntimeError(str(last_error)) from last_error

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

    logger.info(
        "Video processing complete: %s frames -> %s (vcodec=%s reason=%s fallback=%s)",
        frame_count, output_path, vcodec, reason, used_fallback,
    )
