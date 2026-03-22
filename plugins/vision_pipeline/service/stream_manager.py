"""Stream lifecycle manager with worker threads and backpressure.

Each stream gets a dedicated worker thread running the analysis loop.
Frame queues use maxsize=3 — if the pipeline can't keep up, new frames
are dropped silently (backpressure). The frontend always sends the latest
frame, so dropped frames just mean we skip stale data.
"""
import time
import queue
import threading
import uuid
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("vision_pipeline.stream_manager")


@dataclass
class Stream:
    id: str
    source_type: str = "camera"
    started_at: float = 0
    frame_count: int = 0
    analyzed_count: int = 0
    skipped_count: int = 0
    current_fps: float = 0
    status: str = "active"
    last_frame_time: float = 0


class StreamManager:
    def __init__(self, config, frame_analyzer, change_detector,
                 context_buffer, model_tier, adaptive_throttle):
        self.config = config
        self.frame_analyzer = frame_analyzer
        self.change_detector = change_detector
        self.context_buffer = context_buffer
        self.model_tier = model_tier
        self.adaptive_throttle = adaptive_throttle
        self.max_concurrent = getattr(config, 'max_concurrent_streams', 2)
        self.stale_timeout = getattr(config, 'stale_timeout_seconds', 60)
        self.streams: dict[str, Stream] = {}
        self._frame_queues: dict[str, queue.Queue] = {}
        self._shutdown_events: dict[str, threading.Event] = {}
        self._worker_threads: dict[str, threading.Thread] = {}
        self._latest_frames: dict[str, dict] = {}  # stream_id → {frame, timestamp}
        # Start stale stream reaper
        self._reaper_shutdown = threading.Event()
        self._reaper_thread = threading.Thread(target=self._reap_stale_streams, daemon=True)
        self._reaper_thread.start()

    def start_stream(self, stream_id: str = None, source_type: str = "camera") -> Stream:
        active = [s for s in self.streams.values() if s.status in ("active", "paused")]
        if len(active) >= self.max_concurrent:
            raise RuntimeError(f"Cannot start stream: max concurrent streams ({self.max_concurrent}) reached")

        if stream_id is None:
            stream_id = str(uuid.uuid4())[:8]

        stream = Stream(id=stream_id, source_type=source_type,
                        started_at=time.time(), last_frame_time=time.time())
        self.streams[stream_id] = stream
        self._frame_queues[stream_id] = queue.Queue(maxsize=3)
        self._shutdown_events[stream_id] = threading.Event()

        thread = threading.Thread(target=self._analysis_loop, args=(stream_id,), daemon=True)
        self._worker_threads[stream_id] = thread
        thread.start()

        logger.info(f"Stream {stream_id} started (source: {source_type})")
        return stream

    def stop_stream(self, stream_id: str) -> dict:
        if stream_id not in self.streams:
            return {"error": f"Stream {stream_id} not found"}

        stream = self.streams[stream_id]
        self._shutdown_events[stream_id].set()

        thread = self._worker_threads.get(stream_id)
        if thread and thread.is_alive():
            thread.join(timeout=5)

        stats = {
            "stream_id": stream_id,
            "total_frames": stream.frame_count,
            "analyzed": stream.analyzed_count,
            "skipped": stream.skipped_count,
            "duration_seconds": round(time.time() - stream.started_at, 1),
        }

        # Cleanup
        self.streams.pop(stream_id, None)
        self._frame_queues.pop(stream_id, None)
        self._shutdown_events.pop(stream_id, None)
        self._worker_threads.pop(stream_id, None)
        self._latest_frames.pop(stream_id, None)
        self.context_buffer.clear()

        logger.info(f"Stream {stream_id} stopped: {stats}")
        return stats

    def pause_stream(self, stream_id: str):
        if stream_id in self.streams:
            self.streams[stream_id].status = "paused"

    def resume_stream(self, stream_id: str):
        if stream_id in self.streams:
            self.streams[stream_id].status = "active"

    def submit_frame(self, stream_id: str, frame_base64: str) -> dict:
        if stream_id not in self._frame_queues:
            return {"accepted": False, "dropped": False, "queue_depth": 0}

        # Always store latest frame for /frame/latest
        self._latest_frames[stream_id] = {"frame": frame_base64, "timestamp": time.time()}
        self.streams[stream_id].last_frame_time = time.time()
        self.streams[stream_id].frame_count += 1

        try:
            self._frame_queues[stream_id].put_nowait(frame_base64)
            return {"accepted": True, "dropped": False,
                    "queue_depth": self._frame_queues[stream_id].qsize()}
        except queue.Full:
            return {"accepted": True, "dropped": True,
                    "queue_depth": self._frame_queues[stream_id].qsize()}

    def get_latest_frame(self, stream_id: str = None) -> dict | None:
        if stream_id and stream_id in self._latest_frames:
            return {**self._latest_frames[stream_id], "stream_id": stream_id}
        # Return first available
        for sid, data in self._latest_frames.items():
            return {**data, "stream_id": sid}
        return None

    def get_status(self) -> dict:
        return {
            sid: {
                "source_type": s.source_type,
                "status": s.status,
                "frame_count": s.frame_count,
                "analyzed_count": s.analyzed_count,
                "skipped_count": s.skipped_count,
                "current_fps": s.current_fps,
                "duration_seconds": round(time.time() - s.started_at, 1),
                "queue_depth": self._frame_queues.get(sid, queue.Queue()).qsize(),
            }
            for sid, s in self.streams.items()
        }

    def _analysis_loop(self, stream_id: str):
        """Worker thread main loop for a stream."""
        import io
        import base64
        from PIL import Image

        shutdown = self._shutdown_events[stream_id]
        frame_queue = self._frame_queues[stream_id]

        while not shutdown.is_set():
            # 1. Get frame from queue
            try:
                frame_base64 = frame_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            stream = self.streams.get(stream_id)
            if not stream or stream.status == "paused":
                continue

            # 2. Check throttle
            if self.adaptive_throttle.is_paused:
                stream.skipped_count += 1
                continue

            # 3. Decode frame once
            try:
                img_bytes = base64.b64decode(frame_base64)
                img = Image.open(io.BytesIO(img_bytes))
            except Exception as e:
                logger.warning(f"Failed to decode frame for stream {stream_id}: {e}")
                stream.skipped_count += 1
                continue

            # 4. Change detection
            should_process, reason = self.change_detector.should_process(img)
            if not should_process:
                stream.skipped_count += 1
                self.adaptive_throttle.record_scene_state(changed=False)
                continue

            self.adaptive_throttle.record_scene_state(changed=True)

            # 5. Model selection
            trigger = "change_detected" if reason == "visual_change" else "background"
            model, prompt = self.model_tier.select_model(trigger)

            # 6. Vision inference
            result = self.frame_analyzer.analyze(img, model, prompt)
            self.adaptive_throttle.record_inference(result.inference_ms)

            # 7. Update context buffer
            if result.description:
                self.context_buffer.add(result)
                self.change_detector.update_last_description(result.description)

            # 8. Update stats
            stream.analyzed_count += 1
            elapsed = time.time() - stream.started_at
            stream.current_fps = stream.analyzed_count / elapsed if elapsed > 0 else 0

            # 9. Sleep per throttle interval
            interval = self.adaptive_throttle.get_interval()
            if interval < float('inf'):
                shutdown.wait(timeout=interval)

    def _reap_stale_streams(self):
        """Background reaper: auto-stop streams with no frames for stale_timeout."""
        while not self._reaper_shutdown.is_set():
            self._reaper_shutdown.wait(timeout=30)
            if self._reaper_shutdown.is_set():
                break
            now = time.time()
            stale = [sid for sid, s in self.streams.items()
                     if s.status == "active" and now - s.last_frame_time > self.stale_timeout]
            for sid in stale:
                logger.warning(f"Reaping stale stream {sid} (no frames for {self.stale_timeout}s)")
                self.stop_stream(sid)

    def shutdown(self):
        """Stop all streams and reaper."""
        self._reaper_shutdown.set()
        for sid in list(self.streams.keys()):
            self.stop_stream(sid)
