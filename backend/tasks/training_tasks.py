
import json
import logging
import math
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from celery import shared_task
from celery.exceptions import Retry
from sqlalchemy import or_

from backend.services.training.scripts import dataset_formats
from backend.utils.clock import utcnow

try:
    from backend.models import db, TrainingJob, DeviceProfile, TrainingDataset
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType, ProcessStatus
    from backend.utils.progress_emitter import emit_progress_event
except ImportError as e:
    logging.error(f"Failed to import dependencies: {e}")
    db = TrainingJob = DeviceProfile = TrainingDataset = None
    get_unified_progress = None
    ProcessType = None
    ProcessStatus = None
    emit_progress_event = None

logger = logging.getLogger(__name__)

TRAINING_DIR = Path(os.environ.get('GUAARDVARK_ROOT', '.')) / "training"
PROCESSED_DIR = TRAINING_DIR / "processed"
# The transcript parser ships with the training plugin in this checkout.
PARSER_DIR = Path(__file__).resolve().parents[2] / "plugins" / "training" / "scripts"
MODELS_DIR = TRAINING_DIR / "models"
MODELFILES_DIR = Path(os.environ.get('GUAARDVARK_ROOT', '.')) / "data" / "modelfiles"

MODEL_TEMPLATES = {
    'llama-3': 'llama-3.1-instruct.modelfile',
    'llama3': 'llama-3.1-instruct.modelfile',
    'gemma-3': 'gemma-3-text.modelfile',
    'gemma3': 'gemma-3-text.modelfile',
    'gemma-2': 'gemma-3-text.modelfile',
    'gemma2': 'gemma-3-text.modelfile',
}

# Fields the filter reads a pair's quality score from, first match wins.
# plugins/training/scripts/quality_filter.py writes _quality_score; a plain
# "score" covers datasets scored by other tools.
PAIR_SCORE_FIELDS = ("_quality_score", "score")
MIN_SCORE_NOT_APPLIED = "min_score not applied: pairs have no score"


def _pair_score(pair: dict):
    """The pair's numeric quality score, or None when it carries none."""
    for field in PAIR_SCORE_FIELDS:
        value = pair.get(field)
        if value is None or isinstance(value, bool):
            continue
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        if score == score:  # NaN is no score
            return score
    return None


def _update_job_status(job_id: str, **kwargs):
    if not db or not TrainingJob:
        logger.warning("Cannot update job status: models not available")
        return
    
    try:
        from flask import current_app
        with current_app.app_context():
            job = db.session.query(TrainingJob).filter(
                TrainingJob.job_id == job_id
            ).first()
            
            if not job:
                logger.error(f"Job not found: {job_id}")
                return
            
            for key, value in kwargs.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            
            db.session.commit()
            logger.debug(f"Updated job {job_id}: {kwargs}")
    except Exception as e:
        logger.error(f"Error updating job status: {e}", exc_info=True)
        if db:
            db.session.rollback()


def _emit_progress(job_id: str, progress: int, message: str, status: str = "processing", metrics: dict = None):
    try:
        if emit_progress_event:
            if progress == 0 and status == "processing":
                emit_progress_event(
                    process_id=job_id,
                    progress=0,
                    message=message,
                    status="start",  # Use "start" to create the process
                    process_type="training",
                    additional_data={
                        "job_id": job_id,
                        "metrics": metrics or {}
                    }
                )
            else:
                emit_progress_event(
                    process_id=job_id,
                    progress=progress,
                    message=message,
                    status=status,
                    process_type="training",
                    additional_data={
                        "job_id": job_id,
                        "metrics": metrics or {}
                    }
                )
        elif get_unified_progress:
            progress_system = get_unified_progress()
            if progress_system:
                from backend.utils.unified_progress_system import ProcessStatus
                existing = progress_system.get_process(job_id)
                if not existing:
                    progress_system.create_process(
                        ProcessType.TRAINING,
                        message,
                        additional_data={"job_id": job_id, "metrics": metrics or {}},
                        process_id=job_id
                    )
                
                if status == "complete":
                    progress_system.complete_process(job_id, message, {"job_id": job_id, "metrics": metrics or {}})
                elif status == "error":
                    progress_system.error_process(job_id, message, {"job_id": job_id, "metrics": metrics or {}})
                elif status == "cancelled":
                    progress_system.cancel_process(job_id, message, {"job_id": job_id, "metrics": metrics or {}})
                else:
                    progress_system.update_process(job_id, progress, message, {"job_id": job_id, "metrics": metrics or {}})
    except Exception as e:
        logger.warning(f"Could not emit progress: {e}")


# Shares of the full pipeline's progress bar the parse and training steps fill.
PIPELINE_PARSE_PROGRESS = (5, 20)
PIPELINE_TRAIN_PROGRESS = (30, 80)
PIPELINE_EXPORT_PROGRESS = (80, 90)
PIPELINE_IMPORT_PROGRESS = (90, 100)


def parsed_datasets_dir() -> Path:
    """Where parsed transcripts are written, created on first use: the
    training datasets folder the Add Dataset picker opens in."""
    from backend.config import STORAGE_DIR
    path = Path(STORAGE_DIR) / "training" / "datasets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pipeline_emitter(job_id: str, in_pipeline: bool, band: tuple):
    """Progress reporter for a step that also runs inside the full pipeline.

    Inside the pipeline the step's 0-100 is mapped into its band of the
    pipeline's bar and only an error is passed on as a status; the pipeline
    reports its own start and completion."""
    def emit(pct, message, status="processing", metrics=None):
        if in_pipeline:
            low, high = band
            pct = min(100, max(0, int(pct)))
            _emit_progress(job_id, low + (high - low) * pct // 100, message,
                           "error" if status == "error" else "processing", metrics)
        else:
            _emit_progress(job_id, pct, message, status, metrics)
    return emit


@shared_task(bind=True, name='training.parse_transcripts')
def parse_transcripts_task(self, job_id: str, input_path: str, recursive: bool = True,
                           in_pipeline: bool = False):
    """Parse transcripts into instruction/output pairs in one JSONL file.

    in_pipeline: called by full_training_pipeline_task, which owns the job's
    status, timestamps, Celery task id and config. The parser then reports its
    stage and progress only, and the pipeline records the returned report."""
    logger.info(f"Starting parse_transcripts_task for job {job_id}")
    emit = _pipeline_emitter(job_id, in_pipeline, PIPELINE_PARSE_PROGRESS)

    try:
        if in_pipeline:
            _update_job_status(job_id, pipeline_stage="parsing")
        else:
            _update_job_status(job_id, status="running", pipeline_stage="parsing", started_at=utcnow(), celery_task_id=self.request.id)
        emit(0, "Starting transcript parsing...", "start")

        if str(PARSER_DIR) not in sys.path:
            sys.path.insert(0, str(PARSER_DIR))
        from transcript_parser import TranscriptParser

        out_dir = parsed_datasets_dir()
        parser = TranscriptParser(output_dir=str(out_dir))
        
        input_path_obj = Path(input_path)
        if not input_path_obj.exists():
            raise FileNotFoundError(f"Input path not found: {input_path}")
        
        emit(10, f"Parsing transcripts from {input_path}...")

        if input_path_obj.is_file():
            pairs = parser.parse_file(str(input_path_obj))
        elif input_path_obj.is_dir():
            pairs = []
            pattern = "**/*" if recursive else "*"
            for file_path in input_path_obj.glob(pattern):
                if file_path.is_file() and file_path.suffix in ['.jsonl', '.json', '.md', '.txt', '.html', '.docx']:
                    file_pairs = parser.parse_file(str(file_path))
                    pairs.extend(file_pairs)
                    emit(10 + int(80 * len(pairs) / max(1, len(list(input_path_obj.glob(pattern))))),
                         f"Parsed {len(pairs)} pairs from {file_path.name}...")
        else:
            raise ValueError(f"Invalid input path: {input_path}")

        output_filename = f"training_corpus_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        output_path = out_dir / output_filename

        emit(90, f"Saving {len(pairs)} training pairs...")

        with open(output_path, 'w', encoding='utf-8') as f:
            for pair in pairs:
                f.write(json.dumps(pair) + '\n')

        report = {
            "input_path": input_path,
            "output_path": str(output_path),
            "pairs_count": len(pairs)
        }
        if in_pipeline:
            _update_job_status(job_id, pipeline_stage="parsing", progress=PIPELINE_PARSE_PROGRESS[1])
        else:
            _update_job_status(job_id,
                              status="completed",
                              pipeline_stage="parsing",
                              completed_at=utcnow(),
                              progress=100,
                              config_json=json.dumps(report))

        emit(100, f"Completed: {len(pairs)} training pairs saved to {output_filename}", "complete")

        logger.info(f"Parse task completed for job {job_id}: {len(pairs)} pairs")
        return report

    except Exception as e:
        logger.error(f"Error in parse_transcripts_task: {e}", exc_info=True)
        if not in_pipeline:
            _update_job_status(job_id, status="failed", error_message=str(e))
            _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise


@shared_task(bind=True, name='training.build_folder_dataset')
def build_folder_dataset_task(self, job_id: str, folder: str, kinds=None,
                              pairs_per_1000_words: float = 3, target_pairs="enough",
                              include_large_files=None):
    """Build a messages dataset from a folder of notes (folder_dataset).

    Scan, scrub, chunk, ask the local model for pairs, filter, write. A cancel
    is noticed between chunks and writes nothing. Generation goes through the
    chat model like any chat call, so it waits behind a render the way chat
    does and claims no GPU of its own."""
    import time
    from backend.services.training import folder_dataset as fd
    from backend.services.training.pair_generator import PairGenerator

    started = time.monotonic()
    try:
        _update_job_status(job_id, status="running", pipeline_stage="building",
                           started_at=utcnow(), celery_task_id=self.request.id)
        _emit_progress(job_id, 0, "Scanning the folder...", "start")

        prepared = fd.prepare(folder, kinds, include_large=include_large_files or ())
        scrub = prepared["scrub"]
        _emit_progress(job_id, 4, f"Read {prepared['files']} files ({prepared['words']} words); "
                                  f"scrubbed {scrub['total']} items")
        if not prepared["docs"]:
            raise ValueError("Nothing to read: the folder has no notes of the chosen kinds "
                             "that the import rules let in")

        all_chunks = fd.chunk_docs(prepared["docs"])
        chunks, selection = fd.select_passages(all_chunks, fd.resolve_target(target_pairs), pairs_per_1000_words)
        _emit_progress(job_id, 8, f"Split into {len(all_chunks)} passages; writing pairs for "
                                  f"{len(chunks)} of them from {selection['files_contributing']} files...")

        generator = PairGenerator()
        row_written = {"progress": None, "at": 0.0}

        def on_progress(done, total, pairs):
            progress = 8 + 84 * done // max(1, total)
            metrics = {"chunks_done": done, "chunks": total, "pairs": pairs}
            _emit_progress(job_id, progress, f"Writing pairs: chunk {done} of {total}, {pairs} pairs so far",
                           metrics=metrics)
            # Activity and the Jobs table read the row; write it when the
            # percent moves, and every 30 s so the pair count stays current.
            now = time.monotonic()
            if progress != row_written["progress"] or now - row_written["at"] >= 30:
                row_written.update(progress=progress, at=now)
                _update_job_status(job_id, progress=progress, metrics_json=json.dumps(metrics))

        pairs = fd.generate(chunks, pairs_per_1000_words, generator,
                            should_stop=_job_stop_check(job_id), on_progress=on_progress)

        _emit_progress(job_id, 93, f"Filtering {len(pairs)} pairs...")
        kept, dropped = fd.filter_pairs(pairs)
        if not kept:
            raise ValueError(f"No pairs survived the filter ({len(pairs)} written, "
                             f"dropped: {dropped})")

        name = fd.dataset_name(folder)
        report = {
            "folder": folder,
            "kinds": prepared["kinds"],
            "pairs_per_1000_words": pairs_per_1000_words,
            "files": prepared["files"],
            "words": prepared["words"],
            "skipped": prepared["skipped"],
            "unreadable": prepared["unreadable"],
            "scrub": scrub,
            "chunks": len(chunks),
            "selection": selection,
            "pairs_written": len(pairs),
            "pairs_kept": len(kept),
            "dropped": dropped,
            "kinds_kept": {k: sum(1 for p in kept if p["kind"] == k)
                           for k in ("specific", "reasoning", "multi_hop", "summary")},
            "model": getattr(generator._llm, "model", None),
            "model_calls": generator.calls,
            "unparsed_answers": generator.unparsed,
            "generation_seconds": round(generator.seconds, 1),
            "seconds": round(time.monotonic() - started, 1),
        }
        output_path, report_path = fd.write(kept, report, name)
        _update_job_status(job_id, status="completed", pipeline_stage="building",
                           completed_at=utcnow(), progress=100,
                           config_json=json.dumps({
                               "input_path": folder,
                               "output_path": output_path,
                               "report_path": report_path,
                               "pairs_count": len(kept),
                               "files": prepared["files"],
                               "chunks": len(chunks),
                               "passages_available": selection["passages_available"],
                               "target_pairs": selection["target_pairs"],
                               "large_files_left_out": len(prepared["skipped"]["large_files"]),
                               "scrubbed": scrub["total"],
                               "dropped": dropped,
                           }))
        _emit_progress(job_id, 100, f"Completed: {len(kept)} pairs in {name}.jsonl", "complete")
        return {"output_path": output_path, "report_path": report_path, "pairs_count": len(kept)}

    except fd.BuildCancelled:
        logger.info(f"Folder dataset build {job_id} cancelled; nothing written")
        _emit_progress(job_id, 0, "Cancelled; nothing written", "cancelled")
        return {"cancelled": True}
    except Exception as e:
        logger.error(f"Error in build_folder_dataset_task: {e}", exc_info=True)
        _update_job_status(job_id, status="failed", error_message=str(e))
        _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise


# Share of the full pipeline's progress bar the filter step fills.
PIPELINE_FILTER_PROGRESS = (20, 30)


@shared_task(bind=True, name='training.filter_dataset')
def filter_dataset_task(self, job_id: str, input_path: str, min_score: float = 0.5,
                        in_pipeline: bool = False):
    """Drop pairs that are empty, too short, too long, or scored below
    min_score. Pairs without a score are kept; when no pair has one, the job
    says min_score was not applied rather than implying it filtered.

    in_pipeline: called by full_training_pipeline_task, which owns the job's
    status, timestamps and config. The filter then reports its stage and
    progress only, and the pipeline records the returned report."""
    logger.info(f"Starting filter_dataset_task for job {job_id}")

    def emit(pct, message, status="processing"):
        if in_pipeline:
            low, high = PIPELINE_FILTER_PROGRESS
            _emit_progress(job_id, low + (high - low) * pct // 100, message, "processing")
        else:
            _emit_progress(job_id, pct, message, status)

    try:
        min_score = float(min_score)
        if in_pipeline:
            _update_job_status(job_id, pipeline_stage="filtering")
        else:
            _update_job_status(job_id, status="running", pipeline_stage="filtering", started_at=utcnow(), celery_task_id=self.request.id)
        emit(0, "Starting dataset filtering...", "start")
        
        input_path_obj = Path(input_path)
        if not input_path_obj.exists():
            raise FileNotFoundError(f"Input path not found: {input_path}")
        
        emit(10, f"Loading dataset from {input_path}...")
        
        # Read in binary and decode per line, so a line that is not UTF-8 or
        # not a JSON object is skipped and counted instead of failing the job.
        pairs = []
        skipped_lines = []
        with open(input_path_obj, 'rb') as f:
            for line_number, raw in enumerate(f, 1):
                if not raw.strip():
                    continue
                try:
                    pair = json.loads(raw.decode('utf-8'))
                except ValueError:
                    pair = None
                if isinstance(pair, dict):
                    pairs.append(pair)
                else:
                    skipped_lines.append(line_number)
        skipped_note = ""
        if skipped_lines:
            plural = "" if len(skipped_lines) == 1 else "s"
            skipped_note = f"{len(skipped_lines)} malformed line{plural} skipped"
        if skipped_lines:
            logger.warning(f"Filter job {job_id}: {skipped_note} in {input_path} "
                           f"(first: lines {skipped_lines[:10]})")

        scores = [_pair_score(pair) for pair in pairs]
        scored_count = sum(1 for score in scores if score is not None)
        min_score_applied = scored_count > 0
        score_rule = f"min_score={min_score}" if min_score_applied else MIN_SCORE_NOT_APPLIED
        if skipped_note:
            score_rule += f"; {skipped_note}"
        emit(30, f"Filtering {len(pairs)} pairs ({score_rule})...")
        
        filtered_pairs = []
        below_min_score = 0
        for i, (pair, score) in enumerate(zip(pairs, scores)):
            if not pair.get("instruction") or not pair.get("output"):
                continue

            inst_len = len(pair.get("instruction", ""))
            out_len = len(pair.get("output", ""))
            if inst_len < 10 or out_len < 10:
                continue
            if inst_len > 10000 or out_len > 10000:
                continue

            if score is not None and score < min_score:
                below_min_score += 1
                continue

            filtered_pairs.append(pair)

            if (i + 1) % 100 == 0:
                emit(30 + int(50 * (i + 1) / len(pairs)),
                     f"Filtered {len(filtered_pairs)}/{i+1} pairs...")

        if min_score_applied:
            score_summary = f"{below_min_score} below min_score {min_score}"
            if scored_count < len(pairs):
                score_summary += f"; {len(pairs) - scored_count} pairs without a score kept"
        else:
            score_summary = MIN_SCORE_NOT_APPLIED
        if skipped_note:
            score_summary += f"; {skipped_note}"

        output_filename = f"filtered_dataset_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        output_path = PROCESSED_DIR / output_filename
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

        emit(90, f"Saving {len(filtered_pairs)} filtered pairs...")

        with open(output_path, 'w', encoding='utf-8') as f:
            for pair in filtered_pairs:
                f.write(json.dumps(pair) + '\n')

        report = {
            "input_path": input_path,
            "output_path": str(output_path),
            "min_score": min_score,
            "min_score_applied": min_score_applied,
            "scored_count": scored_count,
            "below_min_score": below_min_score,
            "skipped_lines": len(skipped_lines),
            "original_count": len(pairs),
            "filtered_count": len(filtered_pairs),
        }
        if not min_score_applied:
            report["min_score_note"] = MIN_SCORE_NOT_APPLIED

        if in_pipeline:
            _update_job_status(job_id, pipeline_stage="filtering", progress=PIPELINE_FILTER_PROGRESS[1])
        else:
            _update_job_status(job_id,
                              status="completed",
                              pipeline_stage="filtering",
                              completed_at=utcnow(),
                              progress=100,
                              config_json=json.dumps(report))

        emit(100,
             f"Completed: {len(filtered_pairs)}/{len(pairs)} pairs passed filtering ({score_summary})",
             "complete")

        logger.info(f"Filter task completed for job {job_id}: {len(filtered_pairs)}/{len(pairs)} pairs ({score_summary})")
        return report
        
    except Exception as e:
        logger.error(f"Error in filter_dataset_task: {e}", exc_info=True)
        if not in_pipeline:
            _update_job_status(job_id, status="failed", error_message=str(e))
            _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise


# Held-out evaluation for text fine-tunes. A slice of the dataset is kept out
# of training; afterwards the mean loss on it is measured for the trained
# adapter and for the base model, so a run that diverged can be told from one
# that learned. A job's config overrides the share with "eval_fraction"
# (0 turns the split off). None of these numbers has been measured against
# training runs yet.
EVAL_SPLIT = {
    # The usual 90/10 train/held-out split.
    "fraction": 0.1,
    # Fewer held-out rows than this and the mean loss rests on a handful of
    # examples, too few for a comparison with the base model to mean much, so
    # the split is skipped and the job says so. At 10% that is datasets under
    # 100 rows.
    "min_rows": 10,
    # Caps what a large dataset gives up and the time the two measuring passes
    # add: 200 rows of chat text is already tens of thousands of tokens.
    "max_rows": 200,
    # Fixed, so a resumed run holds out the same rows as its first attempt.
    "seed": 42,
}

# Export gate. A run whose held-out loss is worse than the base model's by more
# than this share is neither exported to GGUF nor registered in Ollama; it ends
# as WORSE_THAN_BASE with both losses in error_message and its adapter left on
# disk. A job's config overrides the margin with "eval_gate_margin", and
# "export_if_worse": true lets such a run through; a run already held is
# released by POST /api/training/jobs/<id>/export-anyway. A run with no measured
# loss (too small to split, vision, measurement failed) is not gated.
EVAL_GATE = {
    # Room for measurement noise on a held-out set of tens of rows, so a run
    # that only matched its base is not refused; a run that diverged lands far
    # above it. Not yet measured against training runs.
    "margin": 0.05,
}
WORSE_THAN_BASE = "failed: worse than base"


def _held_out_by_source(rows: list, target: int):
    """Row indices to hold out, whole source files at a time, or None.

    A folder build writes several pairs per chunk and several chunks per file,
    so a random row split puts near-copies of one passage on both sides and the
    held-out loss flatters the adapter. When every row names its source file
    (metadata.source_path), files are drawn in a seeded order until the held
    rows reach the target, never above EVAL_SPLIT["max_rows"] and never every
    file. None (and a random row split) for other datasets, or when whole files
    cannot give at least EVAL_SPLIT["min_rows"]."""
    by_file = {}
    for i, raw in enumerate(rows):
        try:
            row = json.loads(raw)
        except (ValueError, TypeError):
            return None
        meta = row.get("metadata") if isinstance(row, dict) else None
        source = meta.get("source_path") if isinstance(meta, dict) else None
        if not isinstance(source, str) or not source:
            return None
        by_file.setdefault(source, []).append(i)
    if len(by_file) < 2:
        return None
    files = sorted(by_file)
    random.Random(EVAL_SPLIT["seed"]).shuffle(files)
    held = set()
    for source in files[:-1]:
        if len(held) >= target:
            break
        if len(held) + len(by_file[source]) <= EVAL_SPLIT["max_rows"]:
            held.update(by_file[source])
    return held if len(held) >= EVAL_SPLIT["min_rows"] else None


def _hold_out_split(data_path: str, out_dir: Path, fraction: float):
    """Split a text dataset into training rows and held-out rows.

    Returns (train_path, eval_path, report). When nothing is held out,
    train_path is data_path unchanged, eval_path is None and report["reason"]
    says why. JSONL rows are copied byte for byte in their original order."""
    path = Path(data_path)
    report = {"fraction": fraction}
    if fraction <= 0:
        report["reason"] = "held-out split turned off (eval_fraction 0)"
        return data_path, None, report

    if path.suffix == ".jsonl":
        with open(path, "rb") as f:
            rows = [line.rstrip(b"\r\n") for line in f if line.strip()]
    elif path.suffix == ".json":
        with open(path, encoding="utf-8") as f:
            records = json.load(f)
        if not isinstance(records, list):
            report["reason"] = "dataset is not a list of rows"
            return data_path, None, report
        rows = [json.dumps(record).encode("utf-8") for record in records]
    else:
        report["reason"] = f"no held-out split for '{path.suffix}' datasets"
        return data_path, None, report

    held = min(int(len(rows) * fraction), EVAL_SPLIT["max_rows"])
    report["total_rows"] = len(rows)
    if held < EVAL_SPLIT["min_rows"]:
        report["reason"] = (
            f"dataset too small for a held-out split: {len(rows)} rows give {held} "
            f"at {fraction:.0%}, fewer than the {EVAL_SPLIT['min_rows']} needed"
        )
        return data_path, None, report

    held_out = _held_out_by_source(rows, held)
    if held_out is not None:
        report["split"] = "by source file"
    else:
        held_out = set(random.Random(EVAL_SPLIT["seed"]).sample(range(len(rows)), held))
        report["split"] = "random rows"
    held = len(held_out)
    out_dir.mkdir(parents=True, exist_ok=True)
    train_path = out_dir / "train.jsonl"
    eval_path = out_dir / "heldout.jsonl"
    with open(train_path, "wb") as train_file, open(eval_path, "wb") as eval_file:
        for i, row in enumerate(rows):
            (eval_file if i in held_out else train_file).write(row + b"\n")
    report.update(train_rows=len(rows) - held, eval_rows=held, eval_path=str(eval_path))
    return str(train_path), str(eval_path), report


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _record_heldout_losses(report: dict, result: dict):
    """Fold the trainer's held-out result into the job's eval report.

    A loss that is not a finite number is stored as None, since the job config
    is read as JSON in the browser and JSON has no NaN. For the adapter that is
    flagged: it is what a run that diverged looks like."""
    if "reason" in result:
        report["reason"] = result["reason"]
        return
    report["rows"] = result.get("rows")
    base, adapter = result.get("base_loss"), result.get("adapter_loss")
    if not _finite(base):
        report["reason"] = "the base model's held-out loss is not a finite number"
        return
    report["base_loss"] = float(base)
    report["adapter_loss"] = float(adapter) if _finite(adapter) else None
    if not _finite(adapter):
        report["adapter_loss_not_finite"] = True
    report["measured"] = True


def _heldout_summary(report: dict) -> str:
    if not report.get("measured"):
        return f"Held-out loss not measured: {report.get('reason', 'no result from the trainer')}"
    adapter = report["adapter_loss"]
    adapter_text = f"{adapter:.4f}" if adapter is not None else "not a finite number"
    return (f"Held-out loss {adapter_text} (base model {report['base_loss']:.4f}) "
            f"on {report['rows']} rows")


def _export_verdict(report: dict, margin: float, export_if_worse: bool, lora_path: str):
    """Whether the trained adapter may be exported, and the sentence saying why."""
    if not report.get("measured"):
        return True, "Export gate skipped: nothing was measured"
    adapter, base = report["adapter_loss"], report["base_loss"]
    if adapter is None:
        detail = f"the held-out loss is not a finite number (base model {base:.4f}), so the run diverged"
    elif adapter > base * (1 + margin):
        worse = (adapter - base) / base if base else math.inf
        detail = (f"held-out loss {adapter:.4f} is {worse:.1%} worse than the base model's "
                  f"{base:.4f} (allowed margin {margin:.0%})")
    else:
        return True, f"Export gate passed (allowed margin {margin:.0%})"
    if export_if_worse:
        return True, f"Export gate overridden by export_if_worse: {detail}"
    return False, (f"Not exported: {detail}. The adapter is kept at {lora_path}; "
                   f"choose Export anyway on the job to export it")


def dataset_training_files(path):
    """The training files a dataset's path names, or why it names none.

    A .jsonl or .json file is used as it is. A folder contributes every .jsonl
    and .json file under it, recursively, in path order, hidden ones skipped.
    Returns (files, reason): files is empty exactly when reason is set."""
    return dataset_formats.list_files(path)


def _combine_training_files(files, out_path: Path) -> str:
    """Write the rows of several training files to one JSONL file.

    Rows keep their file and line order; blank lines, lines that are not a
    JSON object and a .json file that is not a list are left out."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as out:
        for name in files:
            for record in dataset_formats.iter_rows(name):
                out.write(json.dumps(record).encode("utf-8") + b"\n")
    return str(out_path)


def _dataset_data_path(dataset_name: str, dataset_path: str, combined_path: Path, job_config: dict) -> str:
    """Resolve a job's dataset to the one file its trainer reads.

    Several files are combined into ``combined_path``; the files used are
    recorded in ``job_config["dataset_files"]``."""
    files, reason = dataset_training_files(dataset_path)
    if reason:
        raise ValueError(f"Dataset '{dataset_name}' cannot be trained on: {reason}")
    job_config["dataset_files"] = files
    if len(files) == 1:
        return files[0]
    return _combine_training_files(files, combined_path)


# One optimiser step sees batch_size * this many rows.
GRADIENT_ACCUMULATION_STEPS = 4
# The batch size of a job with no device profile and no batch_size of its own.
DEFAULT_BATCH_SIZE = 2

# A job that leaves "steps" out trains for about this many passes over its
# training rows: the usual range for a LoRA fine-tune of a chat model on a
# small instruction set (TRL's and Unsloth's examples train one to three).
# The cap keeps a large dataset to hours rather than days. Not yet measured
# against runs here.
DEFAULT_STEPS = {"epochs": 3, "min": 10, "max": 1000}

# How long a fine-tune waits behind other GPU work (a video render can hold
# the card for hours) before it fails with the reason.
GPU_WAIT_SECONDS = 4 * 3600
# The trainer is stopped this long before the task's own soft time limit
# (24 h), so the task still records why.
TRAINER_TIME_LIMIT_SECONDS = 23 * 3600


def default_max_steps(rows: int, batch_size: int) -> int:
    """Steps for DEFAULT_STEPS["epochs"] passes over ``rows``, within its bounds."""
    per_step = max(1, int(batch_size) * GRADIENT_ACCUMULATION_STEPS)
    steps = math.ceil(max(0, int(rows)) * DEFAULT_STEPS["epochs"] / per_step)
    return max(DEFAULT_STEPS["min"], min(DEFAULT_STEPS["max"], steps))


class TrainingCancelled(Exception):
    """The job was cancelled or deleted before its trainer started."""


class TrainingLibrariesUnavailable(RuntimeError):
    """The training libraries are missing or incomplete, so the run was
    refused before it started; the message says what and where to fix it."""


def _training_libraries_refusal():
    """training_libraries.unavailable_reason, read at call time. The routes
    refuse a job on it too; the task asks again because the libraries can
    change while the task waits in the queue."""
    from backend.services import training_libraries
    return training_libraries.unavailable_reason()


def _job_stop_check(job_id: str):
    """should_stop for the GPU wait and the trainer: True once the job is
    cancelled or deleted. Reads the database in its own app context, so the
    trainer's watcher thread can call it."""
    from flask import current_app
    app = current_app._get_current_object()

    def stopped() -> bool:
        with app.app_context():
            try:
                row = db.session.query(TrainingJob.status).filter(TrainingJob.job_id == job_id).first()
                return row is None or row[0] == "cancelled"
            except Exception:
                logger.warning(f"Could not read job {job_id}'s status", exc_info=True)
                return False
            finally:
                db.session.remove()

    return stopped


def _claim_job(job_id: str, **fields) -> bool:
    """Write fields to the job unless it was cancelled or deleted; False then,
    so a cancel that lands before the task starts is not overwritten."""
    from flask import current_app
    with current_app.app_context():
        try:
            claimed = db.session.query(TrainingJob).filter(
                TrainingJob.job_id == job_id,
                or_(TrainingJob.status.is_(None), TrainingJob.status != "cancelled"),
            ).update(fields, synchronize_session=False)
            db.session.commit()
            return claimed > 0
        except Exception:
            db.session.rollback()
            raise


def _latest_checkpoint(model_dir: Path):
    checkpoint_dir = Path(model_dir) / "checkpoints"
    if not checkpoint_dir.exists():
        return None
    checkpoints = list(checkpoint_dir.glob("checkpoint-*"))
    if not checkpoints:
        return None
    checkpoints.sort(key=lambda x: int(x.name.split("-")[1]) if x.name.split("-")[1].isdigit() else 0)
    return str(checkpoints[-1])


def _run_text_trainer(spec: dict, *, workdir: Path, on_event, on_start, should_stop) -> str:
    """Run the text trainer for one job and return its output folder. The
    trainer is a separate, offline process (training_runner); tests replace
    this function."""
    from backend.services import training_runner
    result = training_runner.run("run", spec, workdir=workdir, on_event=on_event, on_start=on_start,
                                 should_stop=should_stop, time_limit_s=TRAINER_TIME_LIMIT_SECONDS)
    return result["model_dir"]


@shared_task(bind=True, name='training.finetune_model',
             soft_time_limit=86400, time_limit=172800)
def finetune_model_task(self, job_id: str, config: dict, resume: bool = False,
                        in_pipeline: bool = False):
    """Fine-tune the job's base model on its dataset and record the result.

    While the training libraries are missing or incomplete the job is failed
    with the reason before anything else runs (TrainingLibrariesUnavailable).
    A job without data_path in its config trains on its dataset (dataset_id),
    resolved by dataset_training_files. Without "steps" it trains for
    default_max_steps of its rows. The base model is the declared entry's
    snapshot on this machine (training_base_models); nothing is downloaded.

    The GPU is claimed with gpu_session_when_free (Ollama and ComfyUI give
    way when the run's declared VRAM does not fit, and the run waits behind
    other GPU work); the text trainer then runs as its own process, whose pid
    is the job's pid. A cancel (or delete) stops the wait or the trainer and
    the job stays cancelled.

    in_pipeline: called by full_training_pipeline_task, which owns the job's
    status, timestamps and Celery task id and goes on to export. Training then
    reports its stage and progress inside the pipeline's band and leaves the
    job running; a run the export gate holds still ends the job."""
    from backend.services.gpu_resource_policy import GpuWaitStopped, gpu_session_when_free
    from backend.services.job_types import JobKind
    from backend.services.training_runner import TrainerFailed, TrainerStopped

    logger.info(f"Starting finetune_model_task for job {job_id} (resume={resume})")

    emit = _pipeline_emitter(job_id, in_pipeline, PIPELINE_TRAIN_PROGRESS)
    trained_name = None
    stopped = _job_stop_check(job_id)

    # Before the GPU wait and the trainer, and outside the handler below, so
    # a refused job keeps its checkpoint and can be resumed once repaired. A
    # cancel that landed first stays a cancel.
    unavailable = _training_libraries_refusal()
    if unavailable:
        if not _claim_job(job_id, status="failed", error_message=unavailable, pid=None):
            _emit_progress(job_id, 0, "Training cancelled", "cancelled")
            return {"cancelled": True}
        _emit_progress(job_id, 0, f"Error: {unavailable}", "error")
        raise TrainingLibrariesUnavailable(unavailable)

    try:
        if in_pipeline:
            started = _claim_job(job_id, pipeline_stage="training", pid=None)
        else:
            started = _claim_job(job_id, status="running", pipeline_stage="training",
                                 started_at=utcnow(), celery_task_id=self.request.id, pid=None)
        if not started:
            raise TrainingCancelled()
        emit(0, "Starting model fine-tuning...", "start")

        from flask import current_app
        with current_app.app_context():
            job = db.session.query(TrainingJob).filter(
                TrainingJob.job_id == job_id
            ).first()

            if not job:
                raise ValueError(f"Job not found: {job_id}")

            job_config = json.loads(job.config_json) if job.config_json else {}
            device_profile = None
            if job.device_profile_id:
                device_profile = db.session.get(DeviceProfile, job.device_profile_id)
            dataset = None
            if job.dataset_id:
                row = db.session.get(TrainingDataset, job.dataset_id)
                if row:
                    dataset = (row.name, row.path)
            base_model = job.base_model
            output_name = job.output_model_name or f"guaardvark-{base_model.replace('/', '-').replace(':', '-')}"

        trained_name = output_name
        output_dir = MODELS_DIR / output_name
        # The declared model's snapshot on this machine; the trainer never downloads.
        from backend.services import training_base_models
        entry, base_model_path = training_base_models.resolve_for_training(base_model)
        data_path = job_config.get("data_path") or job_config.get("dataset_path")
        images_path = job_config.get("images_path")

        if not data_path and dataset:
            data_path = _dataset_data_path(*dataset, output_dir / "dataset.jsonl", job_config)
            job_config["data_path"] = data_path
            _update_job_status(job_id, config_json=json.dumps(job_config))
            emit(2, f"Training on dataset '{dataset[0]}': {data_path}")

        if not data_path:
            raise ValueError("data_path not found in job config")

        learning_rate = job_config.get("lr", 2e-4)
        batch_size = job_config.get("batch_size", device_profile.max_batch_size if device_profile else DEFAULT_BATCH_SIZE)
        lora_rank = job_config.get("rank", 16)
        max_seq_length = job_config.get("seq_length", device_profile.max_seq_length if device_profile else 2048)

        gate_margin = float(job_config.get("eval_gate_margin", EVAL_GATE["margin"]))
        if gate_margin < 0:
            raise ValueError(f"eval_gate_margin must not be negative (got {gate_margin})")

        eval_report = {"measured": False}
        if images_path:
            train_path, eval_path = data_path, None
            eval_report["reason"] = "vision runs have no held-out evaluation yet"
        else:
            fraction = float(job_config.get("eval_fraction", EVAL_SPLIT["fraction"]))
            if not 0 <= fraction < 1:
                raise ValueError(f"eval_fraction must be at least 0 and below 1 (got {fraction})")
            train_path, eval_path, split = _hold_out_split(
                data_path, output_dir / "heldout", fraction)
            eval_report.update(split)
        if eval_path:
            emit(3, f"Held out {eval_report['eval_rows']} of {eval_report['total_rows']} rows "
                    f"to measure the trained model on")
        else:
            emit(3, _heldout_summary(eval_report))

        max_steps = job_config.get("steps")
        if not max_steps:
            rows = sum(1 for _ in dataset_formats.iter_rows(str(train_path)))
            max_steps = default_max_steps(rows, batch_size)
            job_config["steps_auto"] = {"rows": rows, "epochs": DEFAULT_STEPS["epochs"],
                                        "rows_per_step": batch_size * GRADIENT_ACCUMULATION_STEPS,
                                        "steps": max_steps}
            emit(4, f"{max_steps} steps: about {DEFAULT_STEPS['epochs']} passes over {rows} rows")
        _update_job_status(job_id, total_steps=max_steps, config_json=json.dumps(job_config))

        def progress_callback(step, total_steps, loss, metrics):
            progress = int((step / total_steps) * 100) if total_steps > 0 else 0
            if in_pipeline:
                low, high = PIPELINE_TRAIN_PROGRESS
                job_progress = low + (high - low) * min(100, progress) // 100
            else:
                job_progress = progress
            _update_job_status(job_id,
                             current_step=step,
                             progress=job_progress,
                             metrics_json=json.dumps(metrics))
            loss_text = f"{loss:.4f}" if _finite(loss) else "n/a"
            emit(progress, f"Training step {step}/{total_steps} (loss: {loss_text})", "processing", metrics)

        emit(5, f"Waiting for the GPU to load {entry.get('name', base_model)}...")

        def waiting(reason):
            emit(5, f"Waiting for the GPU: {reason}")

        with gpu_session_when_free(JobKind.TRAINING, str(job_id), wait_s=GPU_WAIT_SECONDS,
                                   on_wait=waiting, should_stop=stopped,
                                   evict_ollama=True, free_comfyui=True,
                                   vram_estimate_mb=entry.get("vram_mb"), require_fit=True,
                                   cross_process=True, lease_seconds=4 * 3600):
            if images_path:
                emit(8, f"Detected vision task. Using vision trainer with images from {images_path}")
                scripts = str(Path(__file__).resolve().parents[1] / "services" / "training" / "scripts")
                if scripts not in sys.path:
                    sys.path.insert(0, scripts)
                from finetune_vision import finetune

                resume_msg = " (resuming from checkpoint)" if resume else ""
                emit(10, f"Starting vision training loop{resume_msg}...")
                model_dir = finetune(
                    base_model=base_model_path,
                    data_path=data_path,
                    image_folder=images_path,
                    output_name=output_name,
                    max_steps=max_steps,
                    learning_rate=learning_rate,
                    batch_size=batch_size,
                    lora_rank=lora_rank,
                    max_seq_length=max_seq_length,
                    progress_callback=progress_callback,
                    resume=resume
                )
            else:
                train_report = {}

                def on_event(event):
                    kind = event.get("event")
                    if kind == "progress":
                        progress_callback(event.get("step") or 0, event.get("total") or max_steps,
                                          event.get("loss"), event.get("metrics") or {})
                    elif kind == "eval":
                        _record_heldout_losses(eval_report, {k: v for k, v in event.items() if k != "event"})
                    elif kind == "dataset":
                        train_report.update({k: v for k, v in event.items() if k != "event"})
                        emit(9, f"Training on {event.get('trained')} rows (loss on {event.get('loss_on')})")

                spec = {
                    "base_model": base_model_path,
                    "base_model_id": base_model,
                    "data_path": train_path,
                    "eval_data_path": eval_path,
                    "output_dir": str(output_dir),
                    "max_steps": max_steps,
                    "learning_rate": learning_rate,
                    "batch_size": batch_size,
                    "gradient_accumulation_steps": GRADIENT_ACCUMULATION_STEPS,
                    "lora_rank": lora_rank,
                    "max_seq_length": max_seq_length,
                    "resume": bool(resume),
                    "load_in_4bit": bool(entry.get("load_in_4bit")),
                    "response_markers": entry.get("response_markers"),
                    "parent_pid": os.getpid(),
                }
                resume_msg = " (resuming from checkpoint)" if resume else ""
                emit(10, f"Starting text training{resume_msg}...")
                model_dir = _run_text_trainer(
                    spec, workdir=output_dir, on_event=on_event,
                    on_start=lambda pid: _update_job_status(job_id, pid=pid),
                    should_stop=stopped)
                if train_report:
                    job_config["train_report"] = train_report

        lora_path = str(Path(model_dir) / "lora")
        heldout = _heldout_summary(eval_report)
        export_allowed, gate_note = _export_verdict(
            eval_report, gate_margin, job_config.get("export_if_worse") is True, lora_path)
        eval_report["gate"] = {"margin": gate_margin, "export_allowed": export_allowed, "note": gate_note}
        job_config["eval"] = eval_report
        logger.info(f"Job {job_id}: {heldout}. {gate_note}")

        checkpoint_path = _latest_checkpoint(model_dir)
        trained = {"lora_path": lora_path, "checkpoint_path": checkpoint_path,
                   "is_resumable": bool(checkpoint_path), "pid": None,
                   "config_json": json.dumps(job_config)}
        if stopped():
            # Cancelled as the run ended: keep the adapter, leave the status.
            _update_job_status(job_id, **trained)
            emit(100, "Training cancelled", "cancelled")
            return {"cancelled": True, "model_dir": model_dir, "lora_path": lora_path}
        if in_pipeline and export_allowed:
            # The pipeline goes on to export; the job stays running.
            _update_job_status(job_id, pipeline_stage="training",
                               progress=PIPELINE_TRAIN_PROGRESS[1], **trained)
        else:
            # A refused run keeps lora_path, so the adapter can be inspected; the
            # export routes and the Training page offer export only to "completed".
            _update_job_status(job_id,
                              status="completed" if export_allowed else WORSE_THAN_BASE,
                              pipeline_stage="training",
                              completed_at=utcnow(),
                              progress=100,
                              error_message=None if export_allowed else gate_note,
                              **trained)

        if export_allowed:
            emit(100, f"Training complete! {heldout}. {gate_note}. Model saved to {model_dir}", "complete")
        else:
            emit(100, f"Training finished. {gate_note}", "error")

        logger.info(f"Training task completed for job {job_id}: {model_dir}")
        return {"model_dir": model_dir, "lora_path": lora_path, "eval": eval_report,
                "export_allowed": export_allowed}

    except Exception as e:
        cancelled = isinstance(e, (TrainingCancelled, TrainerStopped, GpuWaitStopped)) or stopped()
        checkpoint_path = None
        try:
            # The name the run trained under when it got that far; the task
            # arguments carry it only for some callers.
            output_name = trained_name or config.get("output_name") or f"guaardvark-{config.get('base_model', 'model').replace('/', '-')}"
            checkpoint_path = _latest_checkpoint(MODELS_DIR / output_name)
        except Exception:
            pass

        if cancelled:
            logger.info(f"Training job {job_id} cancelled ({type(e).__name__})")
            _update_job_status(job_id, checkpoint_path=checkpoint_path,
                               is_resumable=bool(checkpoint_path), pid=None)
            _emit_progress(job_id, 0, "Training cancelled", "cancelled")
            return {"cancelled": True}

        logger.error(f"Error in finetune_model_task: {e}", exc_info=True)
        message = str(e)
        if isinstance(e, TrainerFailed) and trained_name:
            message += f" Full log: {MODELS_DIR / trained_name / 'train.log'}"
        _update_job_status(
            job_id,
            status="failed",
            error_message=message,
            checkpoint_path=checkpoint_path,
            is_resumable=bool(checkpoint_path),
            pid=None
        )
        _emit_progress(job_id, 0, f"Error: {message}", "error")
        raise


class ExportNotVerified(RuntimeError):
    """Export to Ollama cannot run for the job now; export_block says why
    (not verified for its base model, or the GGUF converter not ready)."""


def export_block(base_model: str, lora_path: str = None):
    """Why a job's adapter may not be exported now, as (code, reason), or None.
    Export stays off for a base model until it has been run live and its entry
    says verified, and goes through GGUF, so it waits for the GGUF converter
    to be installed and checked with the model's family
    (training_gguf_converter.refusal_for)."""
    from backend.services import training_base_models, training_gguf_converter
    where = f" The trained adapter stays at {lora_path}." if lora_path else ""
    if not training_base_models.export_verified(base_model):
        return "EXPORT_NOT_VERIFIED", f"Export to Ollama is not verified for {base_model} yet.{where}"
    blocked = training_gguf_converter.refusal_for(training_base_models.get(base_model))
    if blocked:
        code, reason = blocked
        return code, f"{reason}{where}"
    return None


def export_refusal(base_model: str, lora_path: str = None):
    """export_block's reason alone, or None."""
    block = export_block(base_model, lora_path)
    return block[1] if block else None


def _unsupported_import_reason(version: str) -> str:
    return (f"The installed Ollama {version} cannot import the merged model as safetensors: it "
            "takes safetensors only through its MLX engine, which does not take this model on "
            "this system. Export through GGUF instead (Settings > Training libraries > GGUF "
            "converter).")


# What each Ollama version measured here does with a merged safetensors folder
# (`ollama create --quantize <level>` on <run>/merged), the route a run folder
# merged before Export went through GGUF still imports by. A version that
# cannot import it is refused before `ollama create`, with its reason; a version
# not listed is tried.
OLLAMA_SAFETENSORS_IMPORT = {
    "0.40.1": {
        "imports": False,
        "reason": _unsupported_import_reason("0.40.1"),
        "measured": (
            "2026-10-12, Linux with an NVIDIA GPU, Qwen2.5-1.5B merged with a LoRA adapter "
            "(merge 9.5 s, 2.9 GB): --quantize q4_K_M refused as unsupported (it takes int4, "
            "int8, nvfp4, mxfp4, mxfp8); int4 and unquantised refused with 'unsupported MLX "
            "architecture: Qwen2ForCausalLM'; --force only wrote a client-side copy the "
            "server never listed."
        ),
    },
}
# The two refusals that measurement saw, mapped to the same plain reason when an
# unlisted version answers with them.
_OLLAMA_IMPORT_ERRORS = ("unsupported --quantize", "unsupported MLX architecture")
_OLLAMA_VERSION_MAX_AGE_S = 60.0
_ollama_version = {"at": None, "version": None}


def ollama_server_version():
    """The running Ollama server's version from its /api/version, or None when
    it does not answer. Asked at most once a minute."""
    now = time.monotonic()
    if _ollama_version["at"] is not None and now - _ollama_version["at"] < _OLLAMA_VERSION_MAX_AGE_S:
        return _ollama_version["version"]
    version = None
    try:
        import requests
        from backend.utils.ollama_resource_manager import get_ollama_base_url
        version = requests.get(f"{get_ollama_base_url()}/api/version", timeout=3).json().get("version")
    except Exception as e:  # noqa: BLE001 — an unknown version keeps today's behaviour
        logger.debug(f"Ollama version not read: {e}")
    _ollama_version.update(at=now, version=version)
    return version


def ollama_create_error(tail: str) -> str:
    """The job error for a failed `ollama create`: an import refusal measured
    here gets the plain reason (Ollama's own text kept after it)."""
    if any(marker in tail for marker in _OLLAMA_IMPORT_ERRORS):
        return f"{_unsupported_import_reason(ollama_server_version() or 'here')} ({tail})"
    return f"ollama create failed: {tail}"


def ollama_import_refusal():
    """Why the installed Ollama cannot take a merged export, or None (also when its version is unknown or unmeasured)."""
    entry = OLLAMA_SAFETENSORS_IMPORT.get(ollama_server_version() or "")
    if entry and not entry["imports"]:
        return entry["reason"]
    return None


# Ollama's spelling of each level for `ollama create --quantize` on a merged
# safetensors folder (_create_from_merged); None means unquantised. The levels
# are the ones Ollama documents for importing safetensors. Export itself offers
# the GGUF converter's levels (training_gguf_converter.LEVELS).
OLLAMA_QUANTIZE = {"q4_k_m": "q4_K_M", "q4_k_s": "q4_K_S", "q8_0": "q8_0", "f16": None}
# Folding a 1-3 GB adapter into its base on the CPU takes minutes; this
# bounds a merge that hangs.
MERGE_TIME_LIMIT_SECONDS = 3600


def export_gguf_name(model_dir: Path, level: str) -> Path:
    """Where Export writes a run's GGUF file at a level."""
    return Path(model_dir) / f"{Path(model_dir).name}-{level}.gguf"


@shared_task(bind=True, name='training.export_gguf')
def export_gguf_task(self, job_id: str, model_dir: str, quantization: str = None,
                     in_pipeline: bool = False, then_import: bool = False):
    """First export step: fold the job's LoRA adapter into its bf16 base model
    (model_dir/merged, safetensors) in an offline subprocess on the CPU, then
    convert that folder to one GGUF file at the chosen level with the GGUF
    converter (training_gguf_converter), also on the CPU. import_ollama_task
    then registers the file with `ollama create`, and only after that deletes
    merged/ (_drop_merged); every export merges afresh. Refused, before the
    job is touched, until export is verified for the job's base model and the
    converter is installed; a level the converter does not offer is refused
    with the choices. quantization None means its default level.

    On its own an export ends the job completed (or failed). then_import: the
    first step of the export-to-Ollama chain, so the job stays running for
    import_ollama_task to finish. in_pipeline: called by
    full_training_pipeline_task, which owns the job's status, timestamps and
    Celery task id; the export then reports its stage and progress only."""
    logger.info(f"Starting export_gguf_task for job {job_id}")
    emit = _pipeline_emitter(job_id, in_pipeline, PIPELINE_EXPORT_PROGRESS)

    from backend.services import training_gguf_converter
    from flask import current_app
    with current_app.app_context():
        job = db.session.query(TrainingJob).filter(TrainingJob.job_id == job_id).first()
        base_model = job.base_model if job else None
        previous_gguf = job.gguf_path if job else None
    refusal = export_refusal(base_model, str(Path(model_dir) / "lora"))
    if refusal:
        # Before the job is touched, so it keeps its completed status.
        raise ExportNotVerified(refusal)
    level = training_gguf_converter.level_for(quantization)

    try:
        if in_pipeline:
            _update_job_status(job_id, pipeline_stage="exporting")
        else:
            _update_job_status(job_id, status="running", pipeline_stage="exporting", started_at=utcnow(), celery_task_id=self.request.id)
        emit(0, "Starting export...", "start")

        from backend.services import training_base_models, training_runner
        _, base_path = training_base_models.resolve_for_training(base_model)
        model_dir_path = Path(model_dir)
        lora_dir = model_dir_path / "lora"
        if not lora_dir.is_dir():
            raise FileNotFoundError(f"No trained adapter at {lora_dir}")
        merged = model_dir_path / "merged"

        emit(10, "Merging the adapter into the base model...")
        training_runner.run(
            "merge",
            {"base_model": base_path, "lora_dir": str(lora_dir), "out_dir": str(merged),
             "parent_pid": os.getpid()},
            workdir=model_dir_path, time_limit_s=MERGE_TIME_LIMIT_SECONDS)

        label = training_gguf_converter.LEVELS[level]["label"]
        emit(60, f"Converting the merged model to GGUF ({label})...")
        gguf = training_gguf_converter.convert(
            merged, export_gguf_name(model_dir_path, level), level,
            log_path=model_dir_path / training_runner.LOG_NAME)
        _drop_other_export(previous_gguf, model_dir_path, gguf)

        finished = not (in_pipeline or then_import)
        if in_pipeline:
            _update_job_status(job_id, pipeline_stage="exporting", progress=PIPELINE_EXPORT_PROGRESS[1],
                               gguf_path=str(gguf), quantization_level=level)
        elif then_import:
            _update_job_status(job_id, pipeline_stage="exporting", progress=100,
                               gguf_path=str(gguf), quantization_level=level)
        else:
            _update_job_status(job_id, status="completed", pipeline_stage="exporting", progress=100,
                               completed_at=utcnow(), gguf_path=str(gguf), quantization_level=level)
        size_gb = gguf.stat().st_size / 1e9
        emit(100, f"GGUF saved to {gguf} ({size_gb:.2f} GB)", "complete" if finished else "processing")
        logger.info(f"Export task completed for job {job_id}: {gguf}")
        return {"merged_path": str(merged), "gguf_path": str(gguf), "quantization": level}

    except Exception as e:
        logger.error(f"Error in export_gguf_task: {e}", exc_info=True)
        if not in_pipeline:
            _update_job_status(job_id, status="failed", error_message=str(e))
            _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise


def _drop_other_export(previous: str, model_dir: Path, current: Path) -> None:
    """Delete the GGUF an earlier export of this run wrote at another level,
    so a re-export leaves one file. Only a file Export itself names is touched."""
    from backend.services import training_gguf_converter
    if not previous:
        return
    previous_path = Path(previous)
    ours = {export_gguf_name(model_dir, level) for level in training_gguf_converter.LEVELS}
    if previous_path != current and previous_path in ours and previous_path.is_file():
        previous_path.unlink()
        logger.info(f"Removed the earlier export {previous_path}")


def _drop_merged(model_dir: Path, gguf: Path) -> int:
    """Delete the run's merged copy (model_dir/merged, the bf16 base with the
    adapter folded in) once Ollama has registered the GGUF converted from it,
    and return the bytes freed. Only a GGUF Export wrote in this folder
    counts. Nothing else in the run folder is touched (lora/, checkpoints/,
    heldout/ and the GGUF stay), and the next export merges again."""
    from backend.services import training_gguf_converter
    model_dir = Path(model_dir)
    merged = model_dir / "merged"
    ours = {export_gguf_name(model_dir, level).resolve() for level in training_gguf_converter.LEVELS}
    if Path(gguf).resolve() not in ours or merged.is_symlink() or not merged.is_dir():
        return 0
    freed = 0
    for root, _, files in os.walk(merged):
        for name in files:
            try:
                freed += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    try:
        shutil.rmtree(merged)
    except OSError as e:
        logger.warning(f"Could not remove the merged copy {merged}: {e}")
        return 0
    logger.info(f"Removed the merged copy {merged} ({freed / 1e9:.2f} GB) once Ollama "
                f"registered {Path(gguf).name}")
    return freed


def ollama_modelfile(entry: dict, source: Path) -> str:
    """The Modelfile registering an exported model with Ollama: the GGUF file
    (or a merged folder), the chat template and stop words its base model
    entry declares, and the app's default sampling."""
    from backend.services import sampling_profiles
    template = entry["ollama_template"]
    stops = "\n".join(f'PARAMETER stop "{stop}"' for stop in entry.get("ollama_stop", ()))
    params = sampling_profiles.profile_modelfile_params(sampling_profiles.DEFAULT_PROFILE)
    return f'FROM {source}\n\nTEMPLATE """{template}"""\n\n{stops}\n{params}\n'


def _create_from_gguf(job_id: str, model_dir: Path, gguf: Path, entry: dict, model_name: str,
                      level: str, emit=None) -> dict:
    """`ollama create <name> -f Modelfile` for an exported GGUF file. Ollama
    imports the file at the level it was converted to; it does not quantize a
    GGUF import. emit is the import's progress reporter."""
    from backend.services import training_gguf_converter
    emit = emit or _pipeline_emitter(job_id, False, PIPELINE_IMPORT_PROGRESS)
    emit(20, f"Writing the Modelfile for {model_name}...")
    modelfile = model_dir / "Modelfile"
    modelfile.write_text(ollama_modelfile(entry, gguf), encoding="utf-8")

    command = ["ollama", "create", model_name, "-f", str(modelfile)]
    label = training_gguf_converter.LEVELS.get(level, {}).get("label") or level.upper() or "GGUF"
    emit(50, f"Registering {model_name} with Ollama ({label})...")
    result = subprocess.run(command, capture_output=True, text=True, cwd=str(model_dir),
                            timeout=MERGE_TIME_LIMIT_SECONDS)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-500:]
        raise RuntimeError(f"ollama create failed: {tail}")
    return {"ollama_model_name": model_name, "quantization": level, "gguf_path": str(gguf)}


def _create_from_merged(job_id: str, model_dir: Path, merged: Path, model_name: str,
                        emit=None) -> dict:
    """`ollama create <name> --quantize <level> -f Modelfile` for a merged folder.
    emit is the import's progress reporter."""
    from backend.services import training_base_models
    from flask import current_app
    with current_app.app_context():
        job = db.session.query(TrainingJob).filter(TrainingJob.job_id == job_id).first()
        base_model = job.base_model if job else None
        quantization = ((job.quantization_level if job else None) or "q4_k_m").lower()
    entry = training_base_models.get(base_model)
    if entry is None:
        raise ValueError(f"{base_model} is not a declared base model; its chat template is unknown.")
    blocked = ollama_import_refusal()
    if blocked:
        raise RuntimeError(blocked)
    if quantization not in OLLAMA_QUANTIZE:
        offered = ", ".join(sorted(OLLAMA_QUANTIZE))
        raise ValueError(f"Ollama cannot quantize a merged model to {quantization}; choose one of {offered}.")

    emit = emit or _pipeline_emitter(job_id, False, PIPELINE_IMPORT_PROGRESS)
    emit(20, f"Writing the Modelfile for {model_name}...")
    modelfile = model_dir / "Modelfile"
    modelfile.write_text(ollama_modelfile(entry, merged), encoding="utf-8")

    level = OLLAMA_QUANTIZE[quantization]
    command = ["ollama", "create", model_name, "-f", str(modelfile)]
    if level:
        command += ["--quantize", level]
    emit(50, f"Registering {model_name} with Ollama ({level or 'f16'})...")
    result = subprocess.run(command, capture_output=True, text=True, cwd=str(model_dir),
                            timeout=MERGE_TIME_LIMIT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(ollama_create_error((result.stderr or result.stdout or "").strip()[-500:]))
    return {"ollama_model_name": model_name, "quantization": level or "f16"}


def _detect_model_architecture(model_name: str, gguf_filename: str) -> str:
    combined = f"{model_name} {gguf_filename}".lower()

    for pattern, template in MODEL_TEMPLATES.items():
        if pattern in combined:
            return template

    return 'llama-3.1-instruct.modelfile'


def _generate_modelfile_from_template(template_name: str, gguf_path: Path, mmproj_path: Path = None) -> str:
    template_path = MODELFILES_DIR / template_name

    if not template_path.exists():
        logger.warning(f"Template not found: {template_path}, using fallback")
        content = f"FROM {gguf_path}\n"
        content += "PARAMETER temperature 0.7\n"
        content += "PARAMETER top_p 0.9\n"
        return content

    with open(template_path, 'r') as f:
        content = f.read()

    content = content.replace('{{GGUF_PATH}}', str(gguf_path))

    if mmproj_path and '{{MMPROJ_PATH}}' in content:
        content = content.replace('{{MMPROJ_PATH}}', str(mmproj_path))
    elif '{{MMPROJ_PATH}}' in content:
        lines = content.split('\n')
        content = '\n'.join(line for line in lines if '{{MMPROJ_PATH}}' not in line)

    return content


@shared_task(bind=True, name='training.import_ollama')
def import_ollama_task(self, job_id: str, model_dir: str, model_name: str, in_pipeline: bool = False):
    """Register an exported model with Ollama and finish the job.

    in_pipeline: called by full_training_pipeline_task, which owns the job's
    status, timestamps and Celery task id; the import then reports its stage,
    progress and model name only."""
    logger.info(f"Starting import_ollama_task for job {job_id}")
    emit = _pipeline_emitter(job_id, in_pipeline, PIPELINE_IMPORT_PROGRESS)

    def finish():
        if in_pipeline:
            _update_job_status(job_id, pipeline_stage="importing", progress=PIPELINE_IMPORT_PROGRESS[1],
                               ollama_model_name=model_name)
        else:
            _update_job_status(job_id, status="completed", pipeline_stage="importing",
                               completed_at=utcnow(), progress=100, ollama_model_name=model_name)

    try:
        if in_pipeline:
            _update_job_status(job_id, pipeline_stage="importing")
        else:
            _update_job_status(job_id, status="running", pipeline_stage="importing", started_at=utcnow(), celery_task_id=self.request.id)
        emit(0, "Starting Ollama import...", "start")

        model_dir_obj = Path(model_dir)

        from backend.services import training_base_models
        from flask import current_app
        with current_app.app_context():
            job = db.session.query(TrainingJob).filter(TrainingJob.job_id == job_id).first()
            base_model = job.base_model if job else None
            exported = Path(job.gguf_path) if job and job.gguf_path else None
            level = ((job.quantization_level if job else None) or "").lower()
        entry = training_base_models.get(base_model)
        if entry is not None and exported is not None and exported.is_file():
            created = _create_from_gguf(job_id, model_dir_obj, exported, entry, model_name, level, emit)
            # Only now: until Ollama has the model, a failed step keeps the merged copy.
            freed = _drop_merged(model_dir_obj, exported)
            finish()
            done = f"Ollama import complete: {model_name}"
            if freed:
                done += f" (removed the merged copy, {freed / 1e9:.1f} GB)"
            emit(100, done, "complete")
            logger.info(f"Import task completed for job {job_id}: {model_name} from {exported}")
            return created

        # A run folder merged before Export went through GGUF holds only merged/.
        merged = model_dir_obj / "merged"
        if (merged / "config.json").is_file():
            created = _create_from_merged(job_id, model_dir_obj, merged, model_name, emit)
            finish()
            emit(100, f"Ollama import complete: {model_name}", "complete")
            logger.info(f"Import task completed for job {job_id}: {model_name}")
            return created

        gguf_files = list(model_dir_obj.glob("*.gguf"))
        if not gguf_files:
            raise FileNotFoundError(f"No GGUF file found in {model_dir}")

        main_gguf = None
        mmproj_gguf = None
        for gf in gguf_files:
            if 'mmproj' in gf.name.lower():
                mmproj_gguf = gf
            else:
                main_gguf = gf

        if not main_gguf:
            main_gguf = gguf_files[0]

        emit(20, f"Creating Modelfile for {model_name}...")

        template_name = _detect_model_architecture(model_name, main_gguf.name)

        if mmproj_gguf and 'gemma' in template_name:
            template_name = 'gemma-3-vision.modelfile'

        logger.info(f"Using template: {template_name} for {model_name}")

        modelfile_content = _generate_modelfile_from_template(template_name, main_gguf, mmproj_gguf)

        modelfile_path = model_dir_obj / "Modelfile"
        with open(modelfile_path, 'w') as f:
            f.write(modelfile_content)

        emit(50, f"Importing {model_name} to Ollama...")
        
        import subprocess
        result = subprocess.run(
            ["ollama", "create", model_name, "-f", str(modelfile_path)],
            capture_output=True,
            text=True,
            cwd=str(model_dir_obj)
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"Ollama import failed: {result.stderr}")

        finish()
        emit(100, f"Ollama import complete: {model_name}", "complete")
        
        logger.info(f"Import task completed for job {job_id}: {model_name}")
        return {"ollama_model_name": model_name}
        
    except Exception as e:
        logger.error(f"Error in import_ollama_task: {e}", exc_info=True)
        if not in_pipeline:
            _update_job_status(job_id, status="failed", error_message=str(e))
            _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise


@shared_task(bind=True, name='training.full_pipeline',
             soft_time_limit=259200, time_limit=345600)
def full_training_pipeline_task(self, job_id: str, config: dict):
    logger.info(f"Starting full_training_pipeline_task for job {job_id}")

    try:
        # Every pipeline trains; refused before it parses or filters anything.
        unavailable = _training_libraries_refusal()
        if unavailable:
            raise TrainingLibrariesUnavailable(unavailable)

        _update_job_status(job_id, status="running", started_at=utcnow())
        _emit_progress(job_id, 0, "Starting full training pipeline...", "processing")

        _emit_progress(job_id, 1, "Freeing GPU memory (unloading Ollama models)...", "processing")
        try:
            from backend.services.gpu_resource_coordinator import unload_ollama_models, get_available_vram

            initial_vram = get_available_vram()
            logger.info(f"Initial VRAM: {initial_vram.get('available_mb', 'unknown')} MB available")

            unload_result = unload_ollama_models()
            if unload_result.get("success"):
                models_unloaded = unload_result.get("models_unloaded", [])
                vram_freed = unload_result.get("vram_freed_mb", 0)
                if models_unloaded:
                    logger.info(f"Unloaded Ollama models: {models_unloaded}, freed {vram_freed} MB")
                    _emit_progress(job_id, 2, f"Freed {vram_freed} MB GPU memory", "processing")
                else:
                    logger.info("No Ollama models were loaded")
            else:
                logger.warning(f"Failed to unload Ollama models: {unload_result.get('error')}")
        except Exception as e:
            logger.warning(f"Could not unload Ollama models (non-fatal): {e}")

        from flask import current_app
        with current_app.app_context():
            job = db.session.query(TrainingJob).filter(
                TrainingJob.job_id == job_id
            ).first()
            
            if not job:
                raise ValueError(f"Job not found: {job_id}")
            
            job_config = json.loads(job.config_json) if job.config_json else {}
            job_config.update(config)
        
        parse_output_path = None
        if job_config.get("input_path"):
            _update_job_status(job_id, pipeline_stage="parsing")
            _emit_progress(job_id, 5, "Step 1/5: Parsing transcripts...", "processing")
            
            parse_result = parse_transcripts_task(job_id,
                                                  job_config["input_path"],
                                                  job_config.get("recursive", True),
                                                  in_pipeline=True)
            parse_output_path = parse_result.get("output_path")
            job_config["parse_output"] = parse_output_path
            job_config["parse_report"] = parse_result
        
        filter_output_path = parse_output_path or job_config.get("dataset_path")
        if job_config.get("min_score") is not None and filter_output_path:
            _update_job_status(job_id, pipeline_stage="filtering")
            _emit_progress(job_id, PIPELINE_FILTER_PROGRESS[0], "Step 2/5: Filtering dataset...", "processing")
            
            filter_result = filter_dataset_task(job_id,
                                               filter_output_path,
                                               job_config.get("min_score", 0.5),
                                               in_pipeline=True)
            filter_output_path = filter_result.get("output_path")
            job_config["data_path"] = filter_output_path
            job_config["filter_report"] = filter_result
        elif filter_output_path:
            job_config["data_path"] = filter_output_path
        
        _update_job_status(job_id, pipeline_stage="training")
        _emit_progress(job_id, PIPELINE_FILTER_PROGRESS[1], "Step 3/5: Training model...", "processing")
        
        if not job_config.get("data_path"):
            raise ValueError("No dataset path available for training")
        
        # The job row read above belongs to a session that closed with its
        # app context, so setting attributes on it saves nothing; write
        # through a fresh one so finetune_model_task reads this config.
        _update_job_status(job_id, config_json=json.dumps(job_config))
        
        train_result = finetune_model_task(job_id, job_config, in_pipeline=True)
        model_dir = train_result.get("model_dir")
        if train_result.get("cancelled"):
            return {"cancelled": True, "model_dir": model_dir}

        # Refused by the export gate: finetune_model_task has already set the
        # job's status and error_message with the losses, and kept the adapter.
        if not train_result.get("export_allowed", True):
            logger.warning(f"Pipeline job {job_id} stopped before export: "
                           f"{train_result['eval']['gate']['note']}")
            return {
                "parse_output": parse_output_path,
                "filter_output": filter_output_path if job_config.get("min_score") is not None else None,
                "model_dir": model_dir,
                "gguf_path": None,
                "ollama_model_name": None,
                "eval": train_result.get("eval")
            }

        not_exported = export_refusal(job.base_model, train_result.get("lora_path"))
        if not_exported:
            heldout = _heldout_summary(train_result.get("eval") or {})
            _update_job_status(job_id, status="completed", pipeline_stage="training",
                               completed_at=utcnow(), progress=100)
            _emit_progress(job_id, 100, f"Training complete. {heldout}. {not_exported}", "complete")
            return {
                "parse_output": parse_output_path,
                "filter_output": filter_output_path if job_config.get("min_score") is not None else None,
                "model_dir": model_dir,
                "gguf_path": None,
                "ollama_model_name": None,
                "eval": train_result.get("eval"),
                "export": not_exported,
            }

        _update_job_status(job_id, pipeline_stage="exporting")
        _emit_progress(job_id, 80, "Step 4/5: Exporting to GGUF...", "processing")
        
        export_result = export_gguf_task(job_id,
                                        model_dir,
                                        job_config.get("quantization"),
                                        in_pipeline=True)
        
        _update_job_status(job_id, pipeline_stage="importing")
        _emit_progress(job_id, 90, "Step 5/5: Importing to Ollama...", "processing")
        
        model_name = job.output_model_name or job_config.get("ollama_model_name")
        if not model_name:
            model_name = f"guaardvark-{job.base_model.replace('/', '-').replace(':', '-')}"
        
        import_result = import_ollama_task(job_id, model_dir, model_name, in_pipeline=True)
        
        _update_job_status(job_id,
                          status="completed",
                          pipeline_stage="importing",
                          completed_at=utcnow(),
                          progress=100)
        
        heldout = _heldout_summary(train_result.get("eval") or {})
        _emit_progress(job_id, 100, f"Full pipeline complete! {heldout}", "complete")

        logger.info(f"Full pipeline completed for job {job_id}")
        return {
            "parse_output": parse_output_path,
            "filter_output": filter_output_path if job_config.get("min_score") is not None else None,
            "model_dir": model_dir,
            "gguf_path": export_result.get("gguf_path"),
            "ollama_model_name": import_result.get("ollama_model_name"),
            "eval": train_result.get("eval")
        }
        
    except Exception as e:
        logger.error(f"Error in full_training_pipeline_task: {e}", exc_info=True)
        _update_job_status(job_id, status="failed", error_message=str(e))
        _emit_progress(job_id, 0, f"Error: {str(e)}", "error")
        raise
