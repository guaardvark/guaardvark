"""The video style measure engine on a synthetic clip with five hard cuts.

The clip is six lavfi sources of different luma (2, 3, 4, 2, 3, 4 seconds)
concatenated, each with a sine tone, built by ffmpeg in a tmp dir. Skipped
when ffmpeg is not installed. Nothing reaches a model or the network.
"""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services.video_style import analyze  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                reason="ffmpeg is required")

SOURCES = ("testsrc2", "smptebars", "color=c=0x303030", "testsrc", "rgbtestsrc", "color=c=0xd0d0d0")
DURATIONS = (2, 3, 4, 2, 3, 4)
EXPECTED_CUTS = [2.0, 5.0, 9.0, 11.0, 14.0]


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    d = tmp_path_factory.mktemp("clip")
    listing = d / "list.txt"
    with listing.open("w") as fh:
        for i, (src, dur) in enumerate(zip(SOURCES, DURATIONS)):
            seg = d / f"seg{i}.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"{src}:size=320x180:rate=24:duration={dur}",
                "-f", "lavfi", "-i", f"sine=frequency={220 * (i + 1)}:duration={dur}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "12", "-c:a", "aac", "-shortest", str(seg),
            ], check=True, timeout=120)
            fh.write(f"file '{seg.name}'\n")
    out = d / "synthetic6.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c", "copy", str(out)], check=True, timeout=120)
    return out


@pytest.fixture(scope="module")
def analysis(clip, tmp_path_factory):
    return analyze.analyze_video(clip, tmp_path_factory.mktemp("out"), name="synthetic")


def test_probe_reads_the_container(clip):
    info = analyze.probe(clip)
    assert info["width"] == 320 and info["height"] == 180
    assert 17.5 < info["duration_s"] < 18.5
    assert info["has_audio"] is True


def test_scdet_finds_the_five_cuts(clip):
    found = analyze.detect_shots(clip, 18.0)
    assert len(found["cuts"]) == 5
    for got, want in zip(found["cuts"], EXPECTED_CUTS):
        assert abs(got - want) < 0.15
    assert [s["dur_s"] for s in found["shots"]] == pytest.approx(DURATIONS, abs=0.15)
    assert all(s["transition_in"]["type"] == "cut" for s in found["shots"][1:])


def test_min_shot_length_merges_close_detections(clip):
    found = analyze.detect_shots(clip, 18.0, min_shot_s=3.5)
    assert all(b - a >= 3.5 for a, b in zip(found["cuts"], found["cuts"][1:]))


def test_every_shot_is_measured(analysis):
    assert analysis["status"] == "complete"
    assert len(analysis["shots"]) == 6
    for shot in analysis["shots"]:
        assert len(shot["keyframes"]) == 3
        assert all(os.path.isfile(k["path"]) for k in shot["keyframes"])
        assert shot["look"]["source"] == "cv"
        assert shot["look"]["key"] in ("low", "normal", "high")
        assert shot["motion"]["label"] in analyze.MOTION_LABELS
        assert shot["motion"]["source"] == "cv"
        assert shot["scale"]["source"] in ("cv", "unavailable")


def test_static_sources_read_as_static(analysis):
    labels = {s["i"]: s["motion"]["label"] for s in analysis["shots"]}
    # smptebars, flat colour, rgbtestsrc and flat colour do not move.
    assert labels[1] == "static" and labels[2] == "static" and labels[4] == "static" and labels[5] == "static"


def test_flat_colours_set_the_key(analysis):
    keys = {s["i"]: s["look"]["key"] for s in analysis["shots"]}
    assert keys[2] == "low"
    assert keys[5] == "high"


def test_profile_pacing(analysis):
    cutting = analysis["profile"]["cutting"]
    assert cutting["shot_count"] == 6
    assert cutting["asl_s"] == pytest.approx(3.0, abs=0.1)
    assert cutting["msl_s"] == pytest.approx(3.0, abs=0.1)
    assert cutting["transition_mix"] == {"cut": 1.0}
    assert cutting["shot_len_1f_alpha"] is None  # needs eight shots
    assert sum(cutting["shot_len_hist_log2"]) == pytest.approx(1.0, abs=1e-6)
    assert len(analysis["profile"]["energy_curve"]) == analyze.AUDIO["energy_bins"]


def test_audio_is_measured(analysis):
    audio = analysis["audio"]
    assert audio["lufs_i"] is not None and audio["lufs_i"] > -60
    assert audio["bpm"] > 0
    assert 0.0 <= audio["frac_cuts_on_beat"] <= 1.0
    assert analysis["profile"]["beat_sync"]["bpm"] == audio["bpm"]


def test_provenance_names_the_tools(analysis):
    tools = analysis["provenance"]["tools"]
    assert set(tools) >= {"ffmpeg", "opencv", "librosa", "scikit-learn"}
    assert analysis["provenance"]["measured_at"]
    assert analysis["source"]["sha256"]


def test_colour_temperature_of_grey_is_daylight():
    import numpy as np

    grey = np.full((32, 32, 3), 128, dtype=np.uint8)
    assert 5500 < analyze.colour_temperature_k(grey) < 7500
    assert analyze.colour_temperature_k(np.zeros((32, 32, 3), dtype=np.uint8)) is None


@pytest.fixture(scope="module")
def motion_clips(tmp_path_factory):
    """Pan, tilt, zoom, wobble and a locked crop cut out of one noise still."""
    d = tmp_path_factory.mktemp("motion")
    still = d / "noise.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "nullsrc=s=1280x720:rate=1,geq=random(1)*255:128:128", "-frames:v", "1", str(still)],
                   check=True, timeout=60)
    crops = {
        "pan": "crop=640:360:x='t*120':y=180",
        "tilt": "crop=640:360:x=320:y='t*60'",
        "zoom_in": "crop=640:360:x=320:y=180,zoompan=z='1+0.1*in/24':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=640x360:fps=24",
        "handheld": "crop=640:360:x='320+10*sin(t*37)':y='180+8*sin(t*53)'",
        "static": "crop=640:360:x=320:y=180",
    }
    out = {}
    for label, vf in crops.items():
        clip = d / f"{label}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(still), "-t", "3", "-r", "24",
                        "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(clip)],
                       check=True, timeout=120)
        out[label] = clip
    return out


def test_motion_labels_on_textured_moves(motion_clips):
    for label, clip in motion_clips.items():
        info = analyze.probe(clip)
        shot = {"t0": 0.0, "t1": info["duration_s"], "dur_s": info["duration_s"]}
        motion = analyze.shot_motion(str(clip), shot, info["width"], info["height"])
        assert motion["label"] == label, (label, motion)
    pan = analyze.shot_motion(str(motion_clips["pan"]), {"t0": 0.0, "t1": 3.0, "dur_s": 3.0}, 640, 360)
    assert pan["pan_w_s"] < -0.1 and abs(pan["tilt_w_s"]) < 0.01
