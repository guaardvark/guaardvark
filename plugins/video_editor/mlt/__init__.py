"""MLT XML pipeline for beat-synced Shotcut project assembly.

Implements the four-stage pipeline from the research doc:
  Stage 1 — mlt_parser:   read template.mlt, harvest main_bin media.
  Stage 2 — beat_detector: librosa-based beat / onset extraction.
  Stage 3 — frame_math:    drift-safe seconds → absolute frame conversion.
  Stage 4 — mlt_writer:    emit Shotcut-compatible tractor XML.
"""
