# backend/api/voice_api.py
# Version 2.1: Performance Optimized Voice API with Rate Limiting and Resource Management

import logging
import os
import shutil
import subprocess
import tempfile
import time
import hashlib
import threading
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, Set
import json
import psutil

from flask import Blueprint, current_app, jsonify, request, send_file
from werkzeug.utils import secure_filename
from backend.utils.response_utils import success_response, error_response

# --- Blueprint Definition ---
voice_bp = Blueprint("voice_api", __name__, url_prefix="/api/voice")

logger = logging.getLogger(__name__)

# ── Voice Model Download Progress Tracking ───────────────────────────────
_voice_download_lock = threading.Lock()
_voice_download_status = {
    "is_downloading": False,
    "current_model": None,
    "model_type": None,   # "piper" or "whisper"
    "progress": 0,
    "status": "idle",
    "error": None,
    "speed_mbps": 0,
    "downloaded_mb": 0,
    "total_mb": 0,
}

# Known Whisper model sizes in MB (ggml format)
WHISPER_MODEL_SIZES_MB = {
    "tiny": 75,
    "tiny.en": 75,
    "base": 142,
    "base.en": 142,
    "small": 466,
    "small.en": 466,
    "medium": 1500,
    "medium.en": 1500,
    "large": 2900,
}

# Piper TTS model sizes in MB (approximate)
PIPER_MODEL_SIZES_MB = {
    "kristin": 63,
    "ryan": 85,
    "amy": 63,
    "joe": 63,
    "lessac": 85,
    "libritts": 85,
    "ljspeech": 85,
}

def _test_ffmpeg_audio_encoding(ffmpeg_path: str) -> bool:
    """Test if an FFmpeg binary can actually encode audio (not just report version flags)."""
    try:
        # Generate 0.1s of silence and encode to wav - tests actual encoding capability
        result = subprocess.run(
            [ffmpeg_path, "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
             "-t", "0.1", "-f", "wav", "-y", "/dev/null"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def find_ffmpeg_executable():
    """Find FFmpeg executable, preferring system FFmpeg with full audio encoding capabilities."""
    import shutil

    # Build candidate list: system paths first, then shutil.which
    candidates = [
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",  # macOS Homebrew
    ]
    which_ffmpeg = shutil.which("ffmpeg")
    if which_ffmpeg and which_ffmpeg not in candidates:
        candidates.insert(0, which_ffmpeg)  # Prefer PATH result

    # Test each candidate for actual audio encoding capability
    for ffmpeg_path in candidates:
        try:
            result = subprocess.run([ffmpeg_path, "-version"],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                continue

            # Quick check: config flags (fast) or actual encoding test (reliable)
            has_flags = "enable-libmp3lame" in result.stdout or "enable-libopus" in result.stdout
            if has_flags or _test_ffmpeg_audio_encoding(ffmpeg_path):
                logger.info(f"VOICE API: Using system FFmpeg with audio encoding: {ffmpeg_path}")
                return ffmpeg_path
            else:
                logger.warning(f"VOICE API: Found FFmpeg at {ffmpeg_path} but audio encoding test failed")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue

    # Fallback: Playwright's FFmpeg (limited, warn loudly)
    try:
        import playwright

        playwright_cache_dir = os.path.expanduser("~/.cache/ms-playwright")
        playwright_paths = []

        if os.path.isdir(playwright_cache_dir):
            try:
                playwright_paths = [
                    os.path.join(playwright_cache_dir, d, "ffmpeg-linux")
                    for d in os.listdir(playwright_cache_dir)
                    if d.startswith("ffmpeg-") and os.path.isdir(os.path.join(playwright_cache_dir, d))
                ]
            except OSError:
                pass

        playwright_paths.extend([
            os.path.join(os.path.dirname(playwright.__file__), "driver", "bin", "ffmpeg"),
            os.path.join(os.path.dirname(playwright.__file__), "driver", "ffmpeg-linux"),
        ])

        for path in playwright_paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                if _test_ffmpeg_audio_encoding(path):
                    logger.warning(f"VOICE API: Using Playwright's FFmpeg (limited): {path}")
                    logger.warning("VOICE API: Install system FFmpeg for better audio support: sudo apt-get install ffmpeg")
                    return path
                else:
                    logger.error(f"VOICE API: Playwright FFmpeg at {path} FAILED audio encoding test - skipping")

    except ImportError:
        logger.debug("VOICE API: Playwright not found")

    raise FileNotFoundError(
        "FFmpeg with audio encoding capabilities not found. Please install full FFmpeg:\n"
        "  - Ubuntu/Debian: sudo apt-get install ffmpeg\n"
        "  - macOS: brew install ffmpeg\n"
        "  - Windows: Download from https://ffmpeg.org/\n"
        "Note: Playwright's FFmpeg has limited audio encoding capabilities and may not work for voice conversion."
    )

# =============================================================================
# PERFORMANCE OPTIMIZATION: Rate Limiting and Resource Management
# =============================================================================

class VoiceRateLimiter:
    """Rate limiter for voice API endpoints to prevent system overload."""
    
    def __init__(self):
        self.request_times: Dict[str, list] = {}
        self.lock = threading.Lock()
        
        # Rate limiting configuration
        self.max_requests_per_minute = 10  # Max 10 voice requests per minute
        self.max_concurrent_requests = 3   # Max 3 concurrent voice requests
        self.active_requests: Set[str] = set()
        
    def is_allowed(self, client_id: str = "default") -> tuple[bool, str]:
        """Check if request is allowed based on rate limiting rules."""
        with self.lock:
            now = time.time()
            
            # Clean old requests (older than 1 minute)
            if client_id in self.request_times:
                self.request_times[client_id] = [
                    req_time for req_time in self.request_times[client_id]
                    if now - req_time < 60
                ]
            
            # Check concurrent requests
            if len(self.active_requests) >= self.max_concurrent_requests:
                return False, "Too many concurrent voice requests. Please wait."
            
            # Check rate limit
            if client_id not in self.request_times:
                self.request_times[client_id] = []
            
            if len(self.request_times[client_id]) >= self.max_requests_per_minute:
                return False, "Rate limit exceeded. Please wait before making another voice request."
            
            # Add current request
            self.request_times[client_id].append(now)
            self.active_requests.add(client_id)
            
            return True, "Request allowed"
    
    def release_request(self, client_id: str = "default"):
        """Release a request slot when processing is complete."""
        with self.lock:
            self.active_requests.discard(client_id)

# Global rate limiter instance
rate_limiter = VoiceRateLimiter()

class AudioCache:
    """Cache for processed audio files to avoid re-processing."""
    
    def __init__(self, max_size_mb: int = 100):
        # Create cache directory using config
        from backend.config import CACHE_DIR
        self.cache_dir = os.path.join(CACHE_DIR, "audio")
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.cache_index: Dict[str, dict] = {}
        self.lock = threading.Lock()
        
        # Ensure cache directory exists
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Load existing cache index
        self._load_cache_index()
    
    def _load_cache_index(self):
        """Load cache index from disk."""
        index_file = os.path.join(self.cache_dir, "index.json")
        if os.path.exists(index_file):
            try:
                with open(index_file, 'r') as f:
                    self.cache_index = json.load(f)
            except (OSError, IOError, json.JSONDecodeError):
                self.cache_index = {}
    
    def _save_cache_index(self):
        """Save cache index to disk."""
        index_file = os.path.join(self.cache_dir, "index.json")
        try:
            with open(index_file, 'w') as f:
                json.dump(self.cache_index, f)
        except (OSError, IOError, json.JSONEncodeError) as e:
            logger.warning(f"Failed to save cache index: {e}")
    
    def _get_file_hash(self, file_path: str) -> str:
        """Generate hash for file content."""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def get_cached_result(self, file_path: str, operation: str) -> Optional[str]:
        """Get cached result if available."""
        with self.lock:
            file_hash = self._get_file_hash(file_path)
            cache_key = f"{file_hash}_{operation}"
            
            if cache_key in self.cache_index:
                cached_file = self.cache_index[cache_key]["file"]
                if os.path.exists(cached_file):
                    # Update access time
                    self.cache_index[cache_key]["last_access"] = time.time()
                    self._save_cache_index()
                    return cached_file
            
            return None
    
    def cache_result(self, file_path: str, operation: str, result_file: str):
        """Cache a processing result."""
        with self.lock:
            file_hash = self._get_file_hash(file_path)
            cache_key = f"{file_hash}_{operation}"
            
            # Check cache size and clean if needed
            self._cleanup_cache()
            
            # Add to cache
            self.cache_index[cache_key] = {
                "file": result_file,
                "size": os.path.getsize(result_file),
                "created": time.time(),
                "last_access": time.time()
            }
            
            self._save_cache_index()
    
    def _cleanup_cache(self):
        """Clean up old cache entries to stay within size limit."""
        current_size = sum(entry["size"] for entry in self.cache_index.values())
        
        if current_size > self.max_size_bytes:
            # Sort by last access time (oldest first)
            sorted_entries = sorted(
                self.cache_index.items(),
                key=lambda x: x[1]["last_access"]
            )
            
            # Remove oldest entries until under limit
            for key, entry in sorted_entries:
                try:
                    if os.path.exists(entry["file"]):
                        os.remove(entry["file"])
                    del self.cache_index[key]
                    current_size -= entry["size"]
                    
                    if current_size <= self.max_size_bytes * 0.8:  # Leave 20% buffer
                        break
                except (OSError, IOError) as e:
                    logger.warning(f"Failed to remove cache entry {key}: {e}")

# Global audio cache instance
audio_cache = AudioCache()

class ProcessMonitor:
    """Monitor and manage LLM processes to prevent system overload."""
    
    def __init__(self):
        self.active_processes: Dict[int, dict] = {}
        self.lock = threading.Lock()
        self.max_cpu_percent = 80  # Max CPU usage before throttling
        self.max_memory_percent = 80  # Max memory usage before throttling
    
    def register_process(self, pid: int, process_type: str, metadata: dict = None):
        """Register a new process for monitoring."""
        with self.lock:
            self.active_processes[pid] = {
                "type": process_type,
                "start_time": time.time(),
                "metadata": metadata or {},
                "last_check": time.time()
            }
            logger.info(f"ProcessMonitor: Registered {process_type} process (PID: {pid})")
    
    def unregister_process(self, pid: int):
        """Unregister a process from monitoring."""
        with self.lock:
            if pid in self.active_processes:
                process_info = self.active_processes[pid]
                duration = time.time() - process_info["start_time"]
                logger.info(f"ProcessMonitor: Unregistered {process_info['type']} process (PID: {pid}, duration: {duration:.2f}s)")
                del self.active_processes[pid]
    
    def get_system_status(self) -> dict:
        """Get current system resource status."""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            return {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "memory_available_gb": memory.available / (1024**3),
                "disk_percent": disk.percent,
                "disk_free_gb": disk.free / (1024**3),
                "active_processes": len(self.active_processes),
                "system_overloaded": (
                    cpu_percent > self.max_cpu_percent or 
                    memory.percent > self.max_memory_percent
                )
            }
        except (psutil.Error, OSError) as e:
            logger.error(f"ProcessMonitor: Failed to get system status: {e}")
            return {"error": str(e)}
    
    def kill_all_llm_processes(self) -> dict:
        """Kill all registered LLM processes."""
        killed_processes = []
        failed_processes = []
        
        with self.lock:
            pids_to_kill = list(self.active_processes.keys())
        
        for pid in pids_to_kill:
            try:
                process = psutil.Process(pid)
                process_info = self.active_processes.get(pid, {})
                
                # Try graceful termination first
                process.terminate()
                try:
                    process.wait(timeout=5)
                except psutil.TimeoutExpired:
                    # Force kill if graceful termination fails
                    process.kill()
                    process.wait(timeout=2)
                
                killed_processes.append({
                    "pid": pid,
                    "type": process_info.get("type", "unknown"),
                    "duration": time.time() - process_info.get("start_time", time.time())
                })
                
                self.unregister_process(pid)
                logger.info(f"ProcessMonitor: Successfully killed process {pid}")
                
            except psutil.NoSuchProcess:
                # Process already dead
                self.unregister_process(pid)
                killed_processes.append({
                    "pid": pid,
                    "type": "unknown",
                    "status": "already_terminated"
                })
            except (psutil.Error, OSError) as e:
                failed_processes.append({
                    "pid": pid,
                    "error": str(e)
                })
                logger.error(f"ProcessMonitor: Failed to kill process {pid}: {e}")
        
        return {
            "killed_processes": killed_processes,
            "failed_processes": failed_processes,
            "total_killed": len(killed_processes),
            "total_failed": len(failed_processes)
        }
    
    def cleanup_dead_processes(self):
        """Remove references to dead processes."""
        with self.lock:
            dead_pids = []
            for pid in self.active_processes:
                try:
                    psutil.Process(pid)
                except psutil.NoSuchProcess:
                    dead_pids.append(pid)
            
            for pid in dead_pids:
                del self.active_processes[pid]
                logger.info(f"ProcessMonitor: Cleaned up dead process {pid}")

# Global process monitor instance
process_monitor = ProcessMonitor()

# =============================================================================
# PERFORMANCE OPTIMIZATION: Enhanced Audio Processing
# =============================================================================

def get_client_id(request) -> str:
    """Get client identifier for rate limiting."""
    # Use IP address as client ID
    return request.remote_addr or "unknown"

def check_rate_limit(request) -> tuple[bool, str]:
    """Check rate limit for current request."""
    client_id = get_client_id(request)
    return rate_limiter.is_allowed(client_id)

def release_rate_limit(request):
    """Release rate limit slot for current request."""
    client_id = get_client_id(request)
    rate_limiter.release_request(client_id)

# Test endpoint to verify voice API is accessible
@voice_bp.route("/test", methods=["GET"])
def test_voice_api():
    """Simple test endpoint to verify voice API is accessible."""
    logger.info("VOICE API: Test endpoint called - voice API is accessible")
    return jsonify({
        "status": "success",
        "message": "Voice API is accessible",
        "timestamp": datetime.now().isoformat()
    })

# =============================================================================
# PERFORMANCE MONITORING AND KILL SWITCH ENDPOINTS
# =============================================================================

@voice_bp.route("/system-status", methods=["GET"])
def get_system_status():
    """Get current system resource status and active processes."""
    try:
        status = process_monitor.get_system_status()
        return jsonify({
            "status": "success",
            "system_status": status,
            "timestamp": datetime.now().isoformat()
        })
    except (psutil.Error, OSError) as e:
        logger.error(f"Failed to get system status: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

@voice_bp.route("/kill-all-processes", methods=["POST"])
def kill_all_llm_processes():
    """Kill all active LLM processes (EMERGENCY KILL SWITCH)."""
    try:
        logger.warning("EMERGENCY KILL SWITCH ACTIVATED - Killing all LLM processes")
        
        # Get system status before killing
        before_status = process_monitor.get_system_status()
        
        # Kill all processes
        result = process_monitor.kill_all_llm_processes()
        
        # Get system status after killing
        after_status = process_monitor.get_system_status()
        
        return jsonify({
            "status": "success",
            "message": "All LLM processes terminated",
            "killed_processes": result["killed_processes"],
            "failed_processes": result["failed_processes"],
            "total_killed": result["total_killed"],
            "total_failed": result["total_failed"],
            "before_status": before_status,
            "after_status": after_status,
            "timestamp": datetime.now().isoformat()
        })
    except (psutil.Error, OSError) as e:
        logger.error(f"Failed to kill processes: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

@voice_bp.route("/cleanup-processes", methods=["POST"])
def cleanup_dead_processes():
    """Clean up references to dead processes."""
    try:
        process_monitor.cleanup_dead_processes()
        return jsonify({
            "status": "success",
            "message": "Dead processes cleaned up",
            "timestamp": datetime.now().isoformat()
        })
    except (psutil.Error, OSError) as e:
        logger.error(f"Failed to cleanup processes: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

# Audio format conversion function using Python libraries
def convert_audio_to_wav(input_path, output_path):
    """Convert audio file to WAV format compatible with Whisper.cpp, avoiding ffprobe dependency."""
    # Try pydub first (more reliable, no ffprobe dependency)
    logger.info(f"VOICE API: Converting audio from {input_path} to {output_path} - trying pydub first")
    success, message = convert_audio_to_wav_pydub(input_path, output_path)
    if success:
        return success, message
        
    # Fallback to librosa if pydub fails
    logger.info(f"VOICE API: pydub failed, trying librosa without ffprobe")
    try:
        import librosa
        import soundfile as sf
        
        # Use librosa with minimal dependencies to avoid ffprobe issues
        # Load file directly without extensive metadata inspection
        audio_data, sample_rate = librosa.load(
            input_path, 
            sr=16000, 
            mono=True,
            res_type='kaiser_fast',  # Faster resampling to avoid complex operations
            dtype='float32'
        )
        logger.info(f"VOICE API: Loaded audio - Duration: {len(audio_data)/sample_rate:.2f}s, Sample rate: {sample_rate}Hz")
        
        # Save as 16-bit WAV file
        sf.write(output_path, audio_data, sample_rate, subtype='PCM_16')
        
        # Verify output file was created
        if not os.path.exists(output_path):
            logger.error(f"VOICE API: Converted audio file not found: {output_path}")
            return False, "Converted audio file not created"
        
        converted_size = os.path.getsize(output_path)
        logger.info(f"VOICE API: Audio conversion successful - Output size: {converted_size} bytes")
        return True, "Audio conversion successful"
        
    except ImportError as e:
        logger.error(f"VOICE API: librosa/soundfile not available: {e}")
        # Final fallback to FFmpeg
        return convert_audio_to_wav_ffmpeg(input_path, output_path)
    except (RuntimeError, ValueError, OSError) as e:
        logger.error(f"VOICE API: librosa audio conversion error: {e}")
        # Final fallback to FFmpeg
        return convert_audio_to_wav_ffmpeg(input_path, output_path)

# Fallback pydub conversion function
def convert_audio_to_wav_pydub(input_path, output_path):
    """Fallback pydub-based audio conversion."""
    try:
        from pydub import AudioSegment
        
        logger.info(f"VOICE API: Converting audio with pydub from {input_path} to {output_path}")
        
        # Load audio file (pydub automatically detects format)
        audio = AudioSegment.from_file(input_path)
        logger.info(f"VOICE API: Loaded audio - Duration: {len(audio)}ms, Channels: {audio.channels}, Sample rate: {audio.frame_rate}Hz")
        
        # Convert to Whisper-compatible format:
        # - 16kHz sample rate
        # - Mono (1 channel)  
        # - 16-bit PCM
        audio = audio.set_frame_rate(16000)  # 16kHz sample rate
        audio = audio.set_channels(1)        # Mono audio
        audio = audio.set_sample_width(2)    # 16-bit (2 bytes per sample)
        
        # Export as WAV
        audio.export(output_path, format="wav")
        
        # Verify output file was created
        if not os.path.exists(output_path):
            logger.error(f"VOICE API: Converted audio file not found: {output_path}")
            return False, "Converted audio file not created"
        
        converted_size = os.path.getsize(output_path)
        logger.info(f"VOICE API: Audio conversion successful - Output size: {converted_size} bytes")
        return True, "Audio conversion successful"
        
    except ImportError as e:
        logger.error(f"VOICE API: pydub not available, falling back to FFmpeg: {e}")
        # Last resort: FFmpeg
        return convert_audio_to_wav_ffmpeg(input_path, output_path)
    except (RuntimeError, ValueError, OSError) as e:
        logger.error(f"VOICE API: pydub audio conversion error: {e}")
        return False, f"Audio conversion error: {e}"

# Fallback FFmpeg conversion function
def convert_audio_to_wav_ffmpeg(input_path, output_path):
    """Fallback FFmpeg-based audio conversion."""
    try:
        # Find FFmpeg executable (try Playwright's bundled version first)
        ffmpeg_path = find_ffmpeg_executable()
        
        # Use ffmpeg to convert to 16kHz mono WAV (Whisper-compatible format)
        cmd = [
            ffmpeg_path, "-i", input_path,
            "-ar", "16000",  # 16kHz sample rate
            "-ac", "1",      # mono audio
            "-acodec", "pcm_s16le",  # 16-bit PCM
            output_path, "-y"  # overwrite output file
        ]
        
        logger.info(f"VOICE API: Converting audio format with FFmpeg: {' '.join(cmd)}")
        
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30  # 30 second timeout for conversion
        )
        
        if process.returncode != 0:
            logger.error(f"VOICE API: Audio conversion failed: {process.stderr}")
            return False, f"Audio conversion failed: {process.stderr}"
        
        # Verify output file was created
        if not os.path.exists(output_path):
            logger.error(f"VOICE API: Converted audio file not found: {output_path}")
            return False, "Converted audio file not created"
        
        converted_size = os.path.getsize(output_path)
        logger.info(f"VOICE API: Audio conversion successful - Output size: {converted_size} bytes")
        return True, "Audio conversion successful"
        
    except subprocess.TimeoutExpired:
        logger.error("VOICE API: Audio conversion timed out")
        return False, "Audio conversion timed out"
    except (subprocess.CalledProcessError, OSError, RuntimeError) as e:
        logger.error(f"VOICE API: Audio conversion error: {e}")
        return False, f"Audio conversion error: {e}"

# Local tool paths (relative to backend directory)
WHISPER_CLI_PATH = "tools/voice/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL_PATH = "tools/voice/whisper.cpp/models/ggml-base.bin"
PIPER_MODEL_PATH = "tools/voice/piper-models/en_US-lessac-medium.onnx"

# PERFORMANCE OPTIMIZATION: Voice configuration constants
DEFAULT_VOICE = "lessac"
PIPER_VOICES = {
    "lessac": {
        "name": "Lessac (English US)",
        "description": "Clear American English voice",
        "model": "tools/voice/piper-models/en_US-lessac-medium.onnx"
    }
}

# PERFORMANCE OPTIMIZATION: Whisper enhanced parameters for better accuracy
WHISPER_ENHANCED_PARAMS = {
    "temperature": 0.0,        # Lower temperature for more consistent results
    "temperature_inc": 0.2,    # Incremental temperature for beam search
    "no_speech_thold": 0.6,    # Threshold for detecting speech vs silence
    "entropy_thold": 2.4,      # Entropy threshold for word detection
    "logprob_thold": -1.0,     # Log probability threshold
    "best_of": 5,              # Number of best candidates to consider
    "beam_size": 5,            # Beam search size
    "word_thold": 0.01         # Word probability threshold
}

# PERFORMANCE OPTIMIZATION: Multiple Whisper models for different use cases
WHISPER_MODELS = {
    "tiny": {
        "path": "tools/voice/whisper.cpp/models/ggml-tiny.bin",
        "name": "Tiny (Fastest)",
        "description": "Fastest processing, good for real-time chat",
        "max_audio_seconds": 30,
        "avg_processing_ratio": 0.1  # 10% of audio length
    },
    "tiny.en": {
        "path": "tools/voice/whisper.cpp/models/ggml-tiny.en.bin", 
        "name": "Tiny English (Fastest)",
        "description": "Fastest English-only processing, optimal for voice chat",
        "max_audio_seconds": 30,
        "avg_processing_ratio": 0.08  # 8% of audio length
    },
    "base": {
        "path": "tools/voice/whisper.cpp/models/ggml-base.bin",
        "name": "Base (Balanced)",
        "description": "Good balance of speed and accuracy",
        "max_audio_seconds": 120,
        "avg_processing_ratio": 0.25  # 25% of audio length
    },
    "small": {
        "path": "tools/voice/whisper.cpp/models/ggml-small.bin",
        "name": "Small (Accurate)",
        "description": "More accurate, slower processing",
        "max_audio_seconds": 300,
        "avg_processing_ratio": 0.4  # 40% of audio length
    }
}

# DEFAULT MODEL for performance optimization
DEFAULT_WHISPER_MODEL = "tiny.en"  # Changed from base to tiny.en for performance

# ENHANCED WHISPER PARAMETERS for better speech detection
WHISPER_ENHANCED_PARAMS = {
    "no_speech_thold": 0.2,      # Lowered from 0.4 to 0.2 for better sensitivity (DEBUG: Very lenient)
    "temperature": 0.0,          # Use deterministic output
    "temperature_inc": 0.2,      # Fallback temperature increment
    "entropy_thold": 2.4,        # Entropy threshold for decoder fail
    "logprob_thold": -1.0,       # Log probability threshold
    "best_of": 3,                # Increased from 2 to 3 for better quality
    "beam_size": 5,              # Use beam search for better accuracy
    "word_thold": 0.01,          # Word timestamp probability threshold
    "max_len": 0,                # No character limit per segment
    "audio_ctx": 0,              # Use full audio context
}

# Configuration for Piper TTS voices
PIPER_VOICES = {
    "kristin": {
        "model": "tools/voice/piper-models/en_US-kristin-medium.onnx",
        "name": "Kristin (English US)",
        "description": "Natural female American English voice"
    },
    "ryan": {
        "model": "tools/voice/piper-models/en_US-ryan-high.onnx",
        "name": "Ryan (English US)",
        "description": "Natural male American English voice - High Quality"
    },
    "amy": {
        "model": "tools/voice/piper-models/en_US-amy-medium.onnx",
        "name": "Amy (English US)",
        "description": "Clear female American English voice"
    },
    "joe": {
        "model": "tools/voice/piper-models/en_US-joe-medium.onnx",
        "name": "Joe (English US)",
        "description": "Warm male American English voice"
    },
    "lessac": {
        "model": "tools/voice/piper-models/en_US-lessac-high.onnx",
        "name": "Lessac (English US)",
        "description": "Clear American English voice - High Quality"
    },
    "libritts": {
        "model": "tools/voice/piper-models/en_US-libritts-high.onnx",
        "name": "LibriTTS (English US)",
        "description": "Natural expressive American English voice - High Quality"
    },
    "ljspeech": {
        "model": "tools/voice/piper-models/en_US-ljspeech-high.onnx",
        "name": "LJ Speech (English US)",
        "description": "Clear female American English voice - High Quality"
    }
    # Can add more voices here as we download them
}
DEFAULT_VOICE = "ryan"

# Supported audio formats
SUPPORTED_AUDIO_FORMATS = {
    "mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm"
}

def allowed_audio_file(filename):
    """Check if the uploaded file is an allowed audio format."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in SUPPORTED_AUDIO_FORMATS


def get_audio_duration(file_path):
    """Get audio duration in seconds using ffprobe or estimate from file size."""
    logger.info(f"VOICE API: Getting audio duration for file: {file_path}")
    
    # Try ffprobe first (most accurate)
    try:
        cmd = ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", file_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            duration = float(result.stdout.strip())
            logger.info(f"VOICE API: ffprobe detected duration: {duration:.2f} seconds")
            return duration
        else:
            logger.warning(f"VOICE API: ffprobe failed with return code {result.returncode}: {result.stderr}")
    except FileNotFoundError:
        logger.info("VOICE API: ffprobe not found in system PATH - using fallback duration estimation")
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError, OSError) as e:
        logger.warning(f"VOICE API: ffprobe error: {e} - using fallback duration estimation")
    except (RuntimeError, AttributeError) as e:
        logger.warning(f"VOICE API: Unexpected ffprobe error: {e} - using fallback duration estimation")
    
    # Try Python librosa for accurate duration (if available)
    try:
        import librosa
        duration = librosa.get_duration(path=file_path)
        logger.info(f"VOICE API: librosa detected duration: {duration:.2f} seconds")
        return duration
    except ImportError:
        logger.debug("VOICE API: librosa not available for duration detection")
    except (RuntimeError, ValueError, OSError) as e:
        logger.warning(f"VOICE API: librosa duration detection failed: {e}")
    
    # Try pydub for duration detection
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(file_path)
        duration = len(audio) / 1000.0  # Convert milliseconds to seconds
        logger.info(f"VOICE API: pydub detected duration: {duration:.2f} seconds")
        return duration
    except ImportError:
        logger.debug("VOICE API: pydub not available for duration detection")
    except (RuntimeError, ValueError, OSError) as e:
        logger.warning(f"VOICE API: pydub duration detection failed: {e}")
    
    # Fallback: estimate from file size (rough approximation)
    try:
        file_size = os.path.getsize(file_path)
        # Improved estimate based on typical audio compression ratios
        # WebM/Opus: ~8KB/sec, MP3: ~16KB/sec, WAV: ~176KB/sec
        # Use conservative estimate of ~12KB/sec for voice recordings
        estimated_duration = file_size / (12 * 1024)  # 12KB per second
        estimated_duration = max(1.0, min(estimated_duration, 600))  # Between 1 sec and 10 minutes
        logger.info(f"VOICE API: File size estimation - {file_size} bytes → {estimated_duration:.2f} seconds")
        return estimated_duration
    except (OSError, IOError) as e:
        logger.error(f"VOICE API: File size estimation failed: {e}")
        logger.info("VOICE API: Using default duration assumption of 30 seconds")
        return 30.0  # Conservative default for voice recordings


def select_optimal_whisper_model(audio_duration_seconds, preferred_model=None):
    """
    Select the optimal Whisper model based on audio duration and preferences.
    
    Args:
        audio_duration_seconds: Duration of audio in seconds
        preferred_model: User preferred model (overrides automatic selection)
        
    Returns:
        dict: Selected model configuration
    """
    # If user has a preference, use it (if available)
    if preferred_model and preferred_model in WHISPER_MODELS:
        return WHISPER_MODELS[preferred_model]
    
    # Automatic selection based on audio duration
    if audio_duration_seconds <= 15:
        # Very short audio: prioritize speed
        return WHISPER_MODELS["tiny.en"]
    elif audio_duration_seconds <= 60:
        # Short audio: balance speed and accuracy
        return WHISPER_MODELS["tiny"]
    elif audio_duration_seconds <= 300:
        # Medium audio: prefer accuracy
        return WHISPER_MODELS["base"]
    else:
        # Long audio: use most accurate model
        return WHISPER_MODELS["small"]


def ensure_whisper_model_downloaded(model_config):
    """
    Ensure the required Whisper model is downloaded.
    
    Args:
        model_config: Model configuration dict
        
    Returns:
        tuple: (success: bool, model_path: str, error_message: str)
    """
    backend_path = get_backend_path()
    model_path = os.path.join(backend_path, model_config["path"])
    
    if os.path.exists(model_path):
        return True, model_path, None
    
    # Try to download the model
    try:
        model_name = os.path.basename(model_config["path"]).replace("ggml-", "").replace(".bin", "")
        download_script = os.path.join(backend_path, "tools/voice/whisper.cpp/models/download-ggml-model.sh")
        
        if not os.path.exists(download_script):
            return False, model_path, f"Download script not found: {download_script}"
        
        logger.info(f"Downloading Whisper model: {model_name}")
        cmd = ["bash", download_script, model_name]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=backend_path)
        
        if result.returncode == 0 and os.path.exists(model_path):
            logger.info(f"Successfully downloaded Whisper model: {model_name}")
            return True, model_path, None
        else:
            error_msg = f"Failed to download model {model_name}: {result.stderr}"
            logger.error(error_msg)
            return False, model_path, error_msg
            
    except (subprocess.CalledProcessError, OSError, RuntimeError) as e:
        error_msg = f"Error downloading model: {str(e)}"
        logger.error(error_msg)
        return False, model_path, error_msg

def get_backend_path():
    """Get the absolute path to the backend directory."""
    return os.path.dirname(os.path.abspath(__file__)).replace('/api', '')

def parse_whisper_output(raw_output):
    """Enhanced parsing of Whisper output with better text extraction."""
    if not raw_output:
        return ""
    
    # Find lines that contain the actual transcription (not debug info)
    lines = raw_output.strip().split('\n')
    text_lines = []
    
    for line in lines:
        stripped_line = line.strip()
        # Skip debug/technical lines but keep transcription text
        if (stripped_line and 
            not stripped_line.startswith('whisper_') and
            not stripped_line.startswith('system_info:') and
            not stripped_line.startswith('main:') and
            not stripped_line.startswith('loading model') and
            not stripped_line.startswith('processing') and
            not stripped_line.startswith('backends') and
            'time =' not in stripped_line and
            'use gpu' not in stripped_line and
            'threads' not in stripped_line and
            'model size' not in stripped_line and
            'compute buffer' not in stripped_line and
            'mel length' not in stripped_line and
            'total time' not in stripped_line and
            'load time' not in stripped_line and
            'fallbacks' not in stripped_line and
            'sampling strategy' not in stripped_line and
            '[' not in stripped_line and ']' not in stripped_line):  # Skip timestamp lines
            text_lines.append(stripped_line)
    
    # Join meaningful lines and clean up
    final_text = ' '.join(text_lines).strip()
    
    # Additional cleanup for common whisper artifacts
    final_text = final_text.replace('(music)', '').replace('(applause)', '').replace('(laughter)', '').strip()
    
    return final_text

@voice_bp.route("/speech-to-text", methods=["POST"])
def speech_to_text():
    """Convert uploaded audio file to text using local Whisper.cpp with performance optimizations."""
    logger.info("Voice API: Received speech-to-text request (LOCAL) - PERFORMANCE OPTIMIZED")
    
    # PERFORMANCE OPTIMIZATION: Rate limiting check
    allowed, message = check_rate_limit(request)
    if not allowed:
        return error_response(f"Rate limit exceeded: {message}", 429, "RATE_LIMITED")
    
    # PERFORMANCE OPTIMIZATION: System overload check
    system_status = process_monitor.get_system_status()
    if system_status.get("system_overloaded", False):
        return jsonify({
            "error": "System is currently overloaded. Please wait before making voice requests.",
            "system_status": system_status
        }), 503
    
    try:
        # Check for file upload
        if "audio" not in request.files:
            return error_response("No audio file provided", 400, "MISSING_FILE")
        
        audio_file = request.files["audio"]
        if audio_file.filename == "":
            return error_response("No audio file selected", 400, "EMPTY_FILE")
        
        if not allowed_audio_file(audio_file.filename):
            return error_response("Unsupported audio format", 400, "UNSUPPORTED_FORMAT")
        
        # Get optional model preference from request
        preferred_model = request.form.get('model', DEFAULT_WHISPER_MODEL)
        
        backend_path = get_backend_path()
        whisper_cli = os.path.join(backend_path, WHISPER_CLI_PATH)
        
        # FIX: Ensure we're using the correct path from project root
        if not os.path.exists(whisper_cli):
            # Try alternative path from project root
            project_root = os.path.dirname(backend_path)
            whisper_cli = os.path.join(project_root, WHISPER_CLI_PATH)
        
        # Check if Whisper CLI exists
        if not os.path.exists(whisper_cli):
            return jsonify({"error": "Whisper.cpp not found. Please install it first."}), 500
        
        # SECURITY FIX: Save uploaded file temporarily with secure file creation
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{secure_filename(audio_file.filename.rsplit('.', 1)[1])}") as temp_file:
            audio_file.save(temp_file.name)
            temp_file_path = temp_file.name

        # PERFORMANCE OPTIMIZATION: Check cache first (must use temp_file_path, not filename)
        try:
            cached_result = audio_cache.get_cached_result(temp_file_path, "speech_to_text")
            if cached_result:
                logger.info(f"Voice API: Using cached result for {audio_file.filename}")
                try:
                    with open(cached_result, 'r') as f:
                        cached_text = f.read().strip()
                    # Clean up temp file before returning cached result
                    os.unlink(temp_file_path)
                    return jsonify({
                        "text": cached_text,
                        "cached": True,
                        "processing_time": 0.0
                    })
                except (OSError, IOError, json.JSONDecodeError) as e:
                    logger.warning(f"Voice API: Failed to read cached result: {e}")
        except (OSError, IOError) as e:
            logger.warning(f"Voice API: Cache check failed: {e}")
        
        # DEBUG: Add audio file analysis
        audio_size = os.path.getsize(temp_file_path)
        logger.info(f"Voice API: Audio file analysis - Size: {audio_size} bytes, Format: {audio_file.filename}")
        
        # PERFORMANCE OPTIMIZATION: Audio format conversion with caching
        logger.info("Voice API: Starting audio format conversion...")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as wav_temp_file:
            wav_temp_path = wav_temp_file.name
        
        # Check if we have a cached conversion
        cached_wav = audio_cache.get_cached_result(temp_file_path, "audio_conversion")
        if cached_wav and os.path.exists(cached_wav):
            logger.info(f"Voice API: Using cached audio conversion")
            wav_temp_path = cached_wav
            conversion_success = True
        else:
            conversion_success, conversion_message = convert_audio_to_wav(temp_file_path, wav_temp_path)
            if conversion_success:
                # Cache the conversion result
                audio_cache.cache_result(temp_file_path, "audio_conversion", wav_temp_path)
        
        if not conversion_success:
            logger.error(f"Voice API: Audio conversion failed: {conversion_message}")
            # Clean up temp files
            try:
                os.unlink(temp_file_path)
                if os.path.exists(wav_temp_path) and wav_temp_path != cached_wav:
                    os.unlink(wav_temp_path)
            except (OSError, IOError):
                pass
            return jsonify({"error": f"Audio conversion failed: {conversion_message}"}), 500
        
        logger.info(f"Voice API: Audio conversion successful, converted file: {wav_temp_path}")
        
        # Use the converted WAV file for Whisper processing
        audio_file_for_whisper = wav_temp_path
        
        try:
            # PERFORMANCE OPTIMIZATION: Get audio duration for intelligent model selection
            audio_duration = get_audio_duration(audio_file_for_whisper)
            logger.info(f"Voice API: Audio duration estimated at {audio_duration:.2f} seconds")
            
            # PERFORMANCE OPTIMIZATION: Select optimal model based on audio duration
            selected_model = select_optimal_whisper_model(audio_duration, preferred_model)
            logger.info(f"Voice API: Selected model '{selected_model['name']}' for optimal performance")
            
            # PERFORMANCE OPTIMIZATION: Ensure model is downloaded
            model_available, model_path, error_msg = ensure_whisper_model_downloaded(selected_model)
            if not model_available:
                logger.warning(f"Voice API: Preferred model unavailable: {error_msg}")
                # Fallback to base model if available
                fallback_model = WHISPER_MODELS["base"]
                model_available, model_path, error_msg = ensure_whisper_model_downloaded(fallback_model)
                if not model_available:
                    return jsonify({"error": f"No Whisper model available: {error_msg}"}), 500
                selected_model = fallback_model
                logger.info(f"Voice API: Falling back to model '{selected_model['name']}'")
            
            # Calculate expected processing time for user feedback
            expected_processing_time = audio_duration * selected_model["avg_processing_ratio"]
            logger.info(f"Voice API: Expected processing time: {expected_processing_time:.2f} seconds")
            
            # PERFORMANCE OPTIMIZATION: Adjust timeout based on audio duration and model
            timeout_seconds = max(30, int(expected_processing_time * 3))  # 3x expected time as timeout
            
            # ENHANCED: Run Whisper.cpp for transcription with optimized parameters
            cmd = [
                whisper_cli,
                "-m", model_path,
                "-f", audio_file_for_whisper,
                "--no-timestamps",
                "--threads", "4",  # Use 4 threads for better performance
                "--language", "en" if ".en" in selected_model["path"] else "auto",
                "--no-fallback",
                "--temperature", str(WHISPER_ENHANCED_PARAMS["temperature"]),
                "--temperature-inc", str(WHISPER_ENHANCED_PARAMS["temperature_inc"]),
                "--no-speech-thold", str(WHISPER_ENHANCED_PARAMS["no_speech_thold"]),
                "--entropy-thold", str(WHISPER_ENHANCED_PARAMS["entropy_thold"]),
                "--logprob-thold", str(WHISPER_ENHANCED_PARAMS["logprob_thold"]),
                "--best-of", str(WHISPER_ENHANCED_PARAMS["best_of"]),
                "--beam-size", str(WHISPER_ENHANCED_PARAMS["beam_size"]),
                "--word-thold", str(WHISPER_ENHANCED_PARAMS["word_thold"]),
            ]
            
            # FIX: Set library path for whisper-cli to find libwhisper.so.1 and libggml.so
            whisper_lib_path = os.path.join(backend_path, "tools/voice/whisper.cpp/build/src")
            ggml_lib_path = os.path.join(backend_path, "tools/voice/whisper.cpp/build/ggml/src")
            # FIX: Ensure we're using the correct path from project root
            if not os.path.exists(whisper_lib_path):
                project_root = os.path.dirname(backend_path)
                whisper_lib_path = os.path.join(project_root, "tools/voice/whisper.cpp/build/src")
                ggml_lib_path = os.path.join(project_root, "tools/voice/whisper.cpp/build/ggml/src")
            
            env = os.environ.copy()
            # Include both whisper and ggml library paths
            lib_paths = [whisper_lib_path, ggml_lib_path]
            if "LD_LIBRARY_PATH" in env:
                env["LD_LIBRARY_PATH"] = f"{':'.join(lib_paths)}:{env['LD_LIBRARY_PATH']}"
            else:
                env["LD_LIBRARY_PATH"] = ":".join(lib_paths)
            
            logger.info(f"Running optimized Whisper.cpp with LD_LIBRARY_PATH={whisper_lib_path}: {' '.join(cmd)}")
            start_time = time.time()
            
            # PERFORMANCE OPTIMIZATION: Register process for monitoring
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=backend_path,
                env=env
            )
            
            # Register the process for monitoring
            process_monitor.register_process(
                process.pid, 
                "whisper_stt", 
                {"audio_file": audio_file.filename, "model": selected_model["name"]}
            )
            
            try:
                # Wait for process completion with timeout
                stdout, stderr = process.communicate(timeout=timeout_seconds)
                result = subprocess.CompletedProcess(
                    process.args, process.returncode, stdout, stderr
                )
            finally:
                # Always unregister the process
                process_monitor.unregister_process(process.pid)
            
            processing_time = time.time() - start_time
            logger.info(f"Voice API: Processing completed in {processing_time:.2f} seconds (expected: {expected_processing_time:.2f})")
            
            if result.returncode != 0:
                logger.error(f"Whisper.cpp failed: {result.stderr}")
                return jsonify({"error": f"Speech recognition failed: {result.stderr}"}), 500
            
            # ENHANCED: Parse Whisper output using enhanced parsing function
            final_text = parse_whisper_output(result.stdout)
            
            if not final_text:
                # DEBUG: Save failed audio file for analysis
                debug_dir = os.path.join(backend_path, "debug_audio")
                if not os.path.exists(debug_dir):
                    os.makedirs(debug_dir)
                
                debug_filename = f"failed_audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{secure_filename(audio_file.filename.rsplit('.', 1)[1])}"
                debug_path = os.path.join(debug_dir, debug_filename)
                
                try:
                    import shutil
                    shutil.copy2(temp_file_path, debug_path)
                    logger.warning(f"Voice API: Saved failed audio file for analysis: {debug_path}")
                except Exception as debug_error:
                    logger.warning(f"Voice API: Could not save debug audio file: {debug_error}")
                
                return jsonify({"error": "No speech detected in audio"}), 400
            
            logger.info(f"Voice API: Successfully transcribed audio to text: '{final_text[:100]}...'")
            
            # PERFORMANCE OPTIMIZATION: Cache the result
            try:
                with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as cache_file:
                    cache_file.write(final_text)
                    cache_file_path = cache_file.name
                
                audio_cache.cache_result(temp_file_path, "speech_to_text", cache_file_path)
                logger.info(f"Voice API: Cached transcription result")
            except Exception as cache_error:
                logger.warning(f"Voice API: Failed to cache result: {cache_error}")
            
            # PERFORMANCE OPTIMIZATION: Release rate limit slot
            release_rate_limit(request)
            
            return jsonify({
                "text": final_text,
                "language": "en" if ".en" in selected_model["path"] else "auto",
                "duration": audio_duration,
                "processing_time": processing_time,
                "model_used": selected_model["name"],
                "engine": "whisper.cpp-optimized",
                "cached": False
            })
            
        finally:
            # SECURITY FIX: Ensure temporary file is always cleaned up
            try:
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)
                if os.path.exists(wav_temp_path):
                    os.unlink(wav_temp_path)
            except (OSError, IOError) as e:
                logger.warning(f"Failed to clean up temp file {temp_file_path}: {e}")
    
    except subprocess.TimeoutExpired:
        logger.error("Voice API: Whisper.cpp timeout")
        # SECURITY FIX: Clean up temp file on timeout
        try:
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            if 'wav_temp_path' in locals() and os.path.exists(wav_temp_path):
                os.unlink(wav_temp_path)
        except OSError:
            pass
        return jsonify({"error": "Speech recognition timeout"}), 500
    except (subprocess.CalledProcessError, OSError, RuntimeError) as e:
        logger.error(f"Voice API: Speech-to-text failed: {e}", exc_info=True)
        # SECURITY FIX: Clean up temp file on error
        try:
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            if 'wav_temp_path' in locals() and os.path.exists(wav_temp_path):
                os.unlink(wav_temp_path)
        except (OSError, IOError):
            pass
        return jsonify({"error": f"Speech recognition failed: {str(e)}"}), 500

@voice_bp.route("/text-to-speech", methods=["POST"])
def text_to_speech():
    """Convert text to speech using local Piper TTS."""
    logger.info("Voice API: Received text-to-speech request (LOCAL)")
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request must be JSON"}), 400
        
        text = data.get("text")
        voice = data.get("voice", DEFAULT_VOICE)
        
        if not text:
            return jsonify({"error": "Text is required"}), 400
        
        # Clean text for better speech synthesis
        # Remove markdown formatting and special characters that don't sound natural
        cleaned_text = text
        # Remove markdown bold/italic formatting
        cleaned_text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', cleaned_text)
        cleaned_text = re.sub(r'_{1,3}([^_]+)_{1,3}', r'\1', cleaned_text)
        # Remove markdown headers
        cleaned_text = re.sub(r'#{1,6}\s*([^\n]+)', r'\1', cleaned_text)
        # Remove markdown code blocks and inline code
        cleaned_text = re.sub(r'```[^`]*```', '', cleaned_text)
        cleaned_text = re.sub(r'`([^`]+)`', r'\1', cleaned_text)
        # Remove markdown links but keep the text
        cleaned_text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', cleaned_text)
        # Remove HTML tags
        cleaned_text = re.sub(r'<[^>]*>', '', cleaned_text)
        # Replace multiple spaces/newlines with single space
        cleaned_text = re.sub(r'\s+', ' ', cleaned_text)
        # Remove bullet points and special characters
        cleaned_text = re.sub(r'[•\-\*]\s*', '', cleaned_text)
        cleaned_text = re.sub(r'\n\s*\n', '. ', cleaned_text)
        cleaned_text = re.sub(r'\n', ', ', cleaned_text)
        # Clean up punctuation
        cleaned_text = re.sub(r'\s*\.\s*\.\s*\.', '. ', cleaned_text)
        cleaned_text = re.sub(r'[!]{2,}', '!', cleaned_text)
        cleaned_text = re.sub(r'[?]{2,}', '?', cleaned_text)
        # Trim and ensure proper sentence ending
        cleaned_text = cleaned_text.strip()
        if cleaned_text and not cleaned_text[-1] in '.!?':
            cleaned_text += '.'
        
        # Use cleaned text for TTS
        text_for_tts = cleaned_text if cleaned_text else text
        
        logger.info(f"Voice API: Original text: '{text[:100]}...'")
        logger.info(f"Voice API: Cleaned text: '{text_for_tts[:100]}...')")
        
        # Validate voice parameter
        if voice not in PIPER_VOICES:
            return jsonify({
                "error": f"Invalid voice. Must be one of: {list(PIPER_VOICES.keys())}"
            }), 400
        
        backend_path = get_backend_path()
        voice_config = PIPER_VOICES[voice]
        piper_model = os.path.join(backend_path, voice_config["model"])
        
        # Check if Piper model exists
        if not os.path.exists(piper_model):
            return jsonify({"error": f"Piper model not found: {voice}"}), 500
        
        # SECURITY FIX: Generate output filename with secure temp file creation
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{voice}.wav", prefix="tts_") as temp_file:
            temp_path = temp_file.name
            filename = os.path.basename(temp_path)
        
        try:
            # Run Piper TTS
            cmd = [
                "python", "-m", "piper",
                "--model", piper_model,
                "--output_file", temp_path
            ]
            
            logger.info(f"Running Piper TTS: {' '.join(cmd)}")
            process = subprocess.run(
                cmd,
                input=text_for_tts,
                text=True,
                capture_output=True,
                timeout=30,
                cwd=backend_path
            )
            
            if process.returncode != 0:
                logger.error(f"Piper TTS failed: {process.stderr}")
                return jsonify({"error": f"Text-to-speech failed: {process.stderr}"}), 500
            
            # Check if output file was created
            if not os.path.exists(temp_path):
                return jsonify({"error": "TTS output file was not created"}), 500
            
            logger.info(f"Voice API: Successfully generated speech for text: '{text[:100]}...'")
            
            return jsonify({
                "audio_url": f"/api/voice/audio/{filename}",
                "filename": filename,
                "text": text,
                "voice": voice,
                "engine": "piper-tts"
            })
            
        except subprocess.TimeoutExpired:
            logger.error("Voice API: Piper TTS timeout")
            # SECURITY FIX: Clean up temp file on timeout
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except (OSError, IOError):
                pass
            return jsonify({"error": "Text-to-speech timeout"}), 500
        except (subprocess.CalledProcessError, OSError, RuntimeError) as e:
            logger.error(f"Voice API: Text-to-speech failed: {e}", exc_info=True)
            # SECURITY FIX: Clean up temp file on error
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except (OSError, IOError):
                pass
            return jsonify({"error": f"Text-to-speech failed: {str(e)}"}), 500
            
    except (ValueError, OSError) as e:
        logger.error(f"Voice API: Text-to-speech request failed: {e}", exc_info=True)
        return jsonify({"error": f"Text-to-speech request failed: {str(e)}"}), 500

@voice_bp.route("/audio/<filename>", methods=["GET"])
def get_audio_file(filename):
    """Serve generated audio files."""
    try:
        # SECURITY FIX: Enhanced security checks for filename
        # Only allow specific filename patterns and sanitize filename
        logger.info(f"VOICE API: Audio file request - Original filename: {filename}")
        filename = secure_filename(filename)
        logger.info(f"VOICE API: Audio file request - Secure filename: {filename}")
        
        # Check filename pattern
        starts_with_tts = filename.startswith("tts_")
        starts_with_voice_chat = filename.startswith("voice_chat_")
        ends_with_wav = filename.endswith(".wav")
        ends_with_mp3 = filename.endswith(".mp3")
        
        logger.info(f"VOICE API: Filename validation - starts_with_tts: {starts_with_tts}, starts_with_voice_chat: {starts_with_voice_chat}, ends_with_wav: {ends_with_wav}, ends_with_mp3: {ends_with_mp3}")
        
        if not filename or not (starts_with_tts or starts_with_voice_chat) or not (ends_with_wav or ends_with_mp3):
            logger.error(f"VOICE API: Invalid filename rejected: {filename}")
            return jsonify({"error": "Invalid filename"}), 400
        
        # SECURITY FIX: Validate filename doesn't contain path traversal
        if '/' in filename or '\\' in filename or '..' in filename:
            return jsonify({"error": "Invalid filename"}), 400
        
        temp_dir = current_app.config.get("TEMP_DIR", tempfile.gettempdir())
        file_path = os.path.join(temp_dir, filename)
        
        # SECURITY FIX: Validate that final path is within temp directory
        temp_dir_real = os.path.realpath(temp_dir)
        file_path_real = os.path.realpath(file_path)
        if not file_path_real.startswith(temp_dir_real + os.sep):
            return jsonify({"error": "Invalid file path"}), 400
        
        if not os.path.exists(file_path):
            return jsonify({"error": "Audio file not found"}), 404
        
        # Determine MIME type based on extension
        mime_type = "audio/wav" if filename.endswith(".wav") else "audio/mpeg"
        
        return send_file(
            file_path,
            mimetype=mime_type,
            as_attachment=True,
            download_name=filename
        )
    
    except (OSError, IOError, FileNotFoundError) as e:
        logger.error(f"Voice API: Failed to serve audio file {filename}: {e}")
        return jsonify({"error": "Failed to serve audio file"}), 500

@voice_bp.route("/voices", methods=["GET"])
def get_available_voices():
    """Get list of available TTS voices (only those with installed models)."""
    voices = []
    backend_path = get_backend_path()
    available_default = None
    
    for voice_id, voice_config in PIPER_VOICES.items():
        model_path = os.path.join(backend_path, voice_config["model"])
        is_available = os.path.exists(model_path)
        
        voices.append({
            "id": voice_id,
            "name": voice_config["name"],
            "description": voice_config["description"],
            "available": is_available
        })
        
        # Track if default voice is available
        if voice_id == DEFAULT_VOICE and is_available:
            available_default = voice_id
    
    # If default voice is not available, pick the first available one
    if not available_default:
        for v in voices:
            if v.get("available"):
                available_default = v["id"]
                break
    
    return jsonify({
        "voices": voices,
        "default_voice": available_default or DEFAULT_VOICE,
        "engine": "piper-tts",
        "models_installed": any(v.get("available") for v in voices)
    })

@voice_bp.route("/models", methods=["GET"])
def get_available_models():
    """Get available Whisper models with performance information."""
    try:
        backend_path = get_backend_path()
        available_models = {}
        
        for model_id, model_config in WHISPER_MODELS.items():
            model_path = os.path.join(backend_path, model_config["path"])
            is_available = os.path.exists(model_path)
            
            available_models[model_id] = {
                "name": model_config["name"],
                "description": model_config["description"],
                "available": is_available,
                "path": model_config["path"],
                "max_audio_seconds": model_config["max_audio_seconds"],
                "avg_processing_ratio": model_config["avg_processing_ratio"],
                "file_size": os.path.getsize(model_path) if is_available else 0
            }
        
        return jsonify({
            "models": available_models,
            "default_model": DEFAULT_WHISPER_MODEL,
            "optimization_enabled": True
        })
    
    except (OSError, IOError) as e:
        logger.error(f"Voice API: Model listing failed: {e}")
        return jsonify({
            "error": str(e)
        }), 500


@voice_bp.route("/status", methods=["GET"])
def voice_status():
    """Check voice API status and configuration with performance optimization details."""
    try:
        backend_path = get_backend_path()
        whisper_cli = os.path.join(backend_path, WHISPER_CLI_PATH)
        whisper_dir = os.path.join(backend_path, "tools/voice/whisper.cpp")

        # Check Whisper.cpp installation status
        whisper_cli_available = os.path.exists(whisper_cli)
        whisper_source_available = os.path.exists(os.path.join(whisper_dir, "CMakeLists.txt")) or os.path.exists(os.path.join(whisper_dir, "Makefile"))

        # Check available Whisper models
        available_models = []
        for model_id, model_config in WHISPER_MODELS.items():
            model_path = os.path.join(backend_path, model_config["path"])
            if os.path.exists(model_path):
                available_models.append(model_id)

        whisper_available = whisper_cli_available and len(available_models) > 0

        # Check FFmpeg availability
        ffmpeg_available = shutil.which("ffmpeg") is not None

        # Check Piper TTS availability
        piper_available = True
        available_voices = []
        for voice_id, voice_config in PIPER_VOICES.items():
            model_path = os.path.join(backend_path, voice_config["model"])
            if os.path.exists(model_path):
                available_voices.append(voice_id)
            else:
                piper_available = False

        # Test Piper import
        try:
            import piper
            piper_import_ok = True
        except ImportError:
            piper_import_ok = False
            piper_available = False

        status = "available" if (whisper_available and piper_available) else "partial"
        if not whisper_available and not piper_available:
            status = "unavailable"

        return jsonify({
            "status": status,
            "speech_recognition": whisper_available,
            "text_to_speech": piper_available,
            "piper_import": piper_import_ok,
            "whisper_installed": whisper_cli_available,
            "whisper_source_available": whisper_source_available,
            "whisper_models_available": available_models,
            "ffmpeg_available": ffmpeg_available,
            "supported_formats": list(SUPPORTED_AUDIO_FORMATS),
            "available_voices": available_voices,
            "engine": "local (whisper.cpp + piper-tts)",
            "optimization": {
                "enabled": True,
                "default_model": DEFAULT_WHISPER_MODEL,
                "available_models": available_models,
                "total_models": len(WHISPER_MODELS),
                "intelligent_selection": True
            },
            "paths": {
                "whisper_cli": whisper_cli,
                "backend_path": backend_path
            }
        })
    
    except (OSError, IOError) as e:
        logger.error(f"Voice API: Status check failed: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500 

@voice_bp.route("/stream", methods=["POST"])
def stream_voice_chat():
    """Real-time voice chat: transcribe → chat → TTS → return audio."""
    logger.info("=" * 80)
    logger.info("VOICE API: STREAM ENDPOINT CALLED - Starting voice chat processing")
    logger.info(f"VOICE API: Request method: {request.method}")
    logger.info(f"VOICE API: Request URL: {request.url}")
    logger.info(f"VOICE API: Request headers: {dict(request.headers)}")
    logger.info(f"VOICE API: Request form data: {dict(request.form)}")
    logger.info(f"VOICE API: Request files: {list(request.files.keys())}")
    logger.info("=" * 80)
    
    try:
        # Check for audio file
        if "audio" not in request.files:
            logger.error("VOICE API: No audio file in request")
            return jsonify({"error": "No audio file provided"}), 400
        
        audio_file = request.files["audio"]
        logger.info(f"VOICE API: Audio file received - filename: {audio_file.filename}, content_type: {audio_file.content_type}")
        
        if audio_file.filename == "":
            logger.error("VOICE API: Audio file has empty filename")
            return jsonify({"error": "No audio file selected"}), 400
        
        if not allowed_audio_file(audio_file.filename):
            logger.error(f"VOICE API: Unsupported audio format: {audio_file.filename}")
            return jsonify({"error": "Unsupported audio format"}), 400
        
        # Get chat session ID from request
        session_id = request.form.get('session_id', 'default')
        logger.info(f"VOICE API: Processing for session_id: {session_id}")
        
        # SECURITY FIX: Save uploaded file temporarily with secure file creation
        backend_path = get_backend_path()
        logger.info(f"VOICE API: Backend path resolved to: {backend_path}")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{secure_filename(audio_file.filename.rsplit('.', 1)[1])}") as temp_file:
            audio_file.save(temp_file.name)
            temp_file_path = temp_file.name
        
        logger.info(f"VOICE API: Audio file saved to temporary path: {temp_file_path}")
        
        # DEBUG: Add audio file analysis
        audio_size = os.path.getsize(temp_file_path)
        logger.info(f"VOICE API: Stream audio file analysis - Size: {audio_size} bytes, Format: {audio_file.filename}")
        
        # Basic audio file validation
        if audio_size == 0:
            logger.error("VOICE API: Audio file is empty (0 bytes)")
            return jsonify({"error": "Audio file is empty"}), 400
        elif audio_size < 1000:  # Less than 1KB
            logger.warning(f"VOICE API: Audio file is very small ({audio_size} bytes) - may not contain speech")
        
        # AUDIO FORMAT CONVERSION: Convert WebM/other formats to WAV for Whisper compatibility
        logger.info("VOICE API: Starting audio format conversion...")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as wav_temp_file:
            wav_temp_path = wav_temp_file.name
        
        conversion_success, conversion_message = convert_audio_to_wav(temp_file_path, wav_temp_path)
        
        if not conversion_success:
            logger.error(f"VOICE API: Audio conversion failed: {conversion_message}")
            # Clean up temp files
            try:
                os.unlink(temp_file_path)
                if os.path.exists(wav_temp_path):
                    os.unlink(wav_temp_path)
            except (OSError, IOError) as cleanup_error:
                logger.warning(f"VOICE API: Cleanup error: {cleanup_error}")
            return jsonify({"error": f"Audio conversion failed: {conversion_message}"}), 400
        
        logger.info(f"VOICE API: Audio conversion successful - Using WAV file: {wav_temp_path}")
        
        # Update audio file analysis after conversion
        wav_audio_size = os.path.getsize(wav_temp_path)
        logger.info(f"VOICE API: Converted WAV file analysis - Size: {wav_audio_size} bytes")
        
        try:
            # Step 1: Enhanced transcription using optimized Whisper with better parameters
            whisper_cli = os.path.join(backend_path, WHISPER_CLI_PATH)
            logger.info(f"VOICE API: Checking Whisper CLI at: {whisper_cli}")
            
            if not os.path.exists(whisper_cli):
                logger.error(f"VOICE API: Whisper CLI not found at: {whisper_cli}")
                return jsonify({"error": "Whisper CLI not found"}), 500
            
            # PERFORMANCE OPTIMIZATION: Get audio duration for intelligent model selection
            logger.info("VOICE API: Getting audio duration...")
            audio_duration = get_audio_duration(wav_temp_path)  # Use converted WAV file
            logger.info(f"VOICE API: Stream audio duration estimated at {audio_duration:.2f} seconds")
            
            # Use tiny.en model for speed with robust validation
            logger.info("VOICE API: Selecting Whisper model...")
            model_config = WHISPER_MODELS["tiny.en"]
            logger.info(f"VOICE API: Attempting to use model: {model_config['name']}")
            
            model_available, model_path, error_msg = ensure_whisper_model_downloaded(model_config)
            if not model_available:
                logger.warning(f"VOICE API: Preferred model unavailable: {error_msg}")
                # Fallback to base model if available
                fallback_model = WHISPER_MODELS["base"]
                model_available, model_path, error_msg = ensure_whisper_model_downloaded(fallback_model)
                if not model_available:
                    logger.error(f"VOICE API: No Whisper model available: {error_msg}")
                    return jsonify({"error": f"No Whisper model available: {error_msg}"}), 500
                model_config = fallback_model
                logger.info(f"VOICE API: Falling back to model '{model_config['name']}'")
            
            logger.info(f"VOICE API: Using Whisper model '{model_config['name']}' for streaming chat")
            logger.info(f"VOICE API: Model path: {model_path}")
            
            # ENHANCED: Run Whisper transcription with improved parameters on converted WAV file
            cmd = [
                whisper_cli,
                "-m", model_path,
                "-f", wav_temp_path,  # Use converted WAV file instead of original
                "--language", "en",
                "--threads", "4",
                "--no-timestamps",
                "--no-fallback",
                "--temperature", str(WHISPER_ENHANCED_PARAMS["temperature"]),
                "--temperature-inc", str(WHISPER_ENHANCED_PARAMS["temperature_inc"]),
                "--no-speech-thold", str(WHISPER_ENHANCED_PARAMS["no_speech_thold"]),
                "--entropy-thold", str(WHISPER_ENHANCED_PARAMS["entropy_thold"]),
                "--logprob-thold", str(WHISPER_ENHANCED_PARAMS["logprob_thold"]),
                "--best-of", str(WHISPER_ENHANCED_PARAMS["best_of"]),
                "--beam-size", str(WHISPER_ENHANCED_PARAMS["beam_size"]),
                "--word-thold", str(WHISPER_ENHANCED_PARAMS["word_thold"]),
            ]
            
            # FIX: Set library path for whisper-cli to find libwhisper.so.1 and libggml.so
            whisper_lib_path = os.path.join(backend_path, "tools/voice/whisper.cpp/build/src")
            ggml_lib_path = os.path.join(backend_path, "tools/voice/whisper.cpp/build/ggml/src")
            # FIX: Ensure we're using the correct path from project root
            if not os.path.exists(whisper_lib_path):
                project_root = os.path.dirname(backend_path)
                whisper_lib_path = os.path.join(project_root, "tools/voice/whisper.cpp/build/src")
                ggml_lib_path = os.path.join(project_root, "tools/voice/whisper.cpp/build/ggml/src")
            
            env = os.environ.copy()
            # Include both whisper and ggml library paths
            lib_paths = [whisper_lib_path, ggml_lib_path]
            if "LD_LIBRARY_PATH" in env:
                env["LD_LIBRARY_PATH"] = f"{':'.join(lib_paths)}:{env['LD_LIBRARY_PATH']}"
            else:
                env["LD_LIBRARY_PATH"] = ":".join(lib_paths)
            
            logger.info(f"VOICE API: Running enhanced Whisper transcription with LD_LIBRARY_PATH={whisper_lib_path}: {' '.join(cmd)}")
            start_time = time.time()
            
            # Enhanced timeout based on audio duration
            timeout_seconds = max(15, int(audio_duration * 2))  # 2x audio length as timeout
            logger.info(f"VOICE API: Using timeout of {timeout_seconds} seconds")
            
            process = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                timeout=timeout_seconds,
                cwd=backend_path,
                env=env
            )
            
            processing_time = time.time() - start_time
            logger.info(f"VOICE API: Stream transcription completed in {processing_time:.2f} seconds")
            logger.info(f"VOICE API: Whisper process return code: {process.returncode}")
            
            if process.returncode != 0:
                logger.error(f"VOICE API: Whisper transcription failed with return code {process.returncode}")
                logger.error(f"VOICE API: Whisper stderr: {process.stderr}")
                return jsonify({"error": f"Transcription failed: {process.stderr}"}), 500
            
            # ENHANCED: Extract transcribed text with better parsing
            transcribed_text = parse_whisper_output(process.stdout)
            
            # DEBUG: Enhanced logging for debugging speech detection
            logger.info(f"VOICE API: Raw Whisper stdout length: {len(process.stdout)} characters")
            logger.info(f"VOICE API: Raw Whisper stderr length: {len(process.stderr)} characters")
            logger.info(f"VOICE API: Parsed transcribed text: '{transcribed_text}' (length: {len(transcribed_text)})")
            
            if not transcribed_text:
                # DEBUG: Save failed audio file for analysis
                debug_dir = os.path.join(backend_path, "debug_audio")
                if not os.path.exists(debug_dir):
                    os.makedirs(debug_dir)
                
                debug_filename = f"stream_failed_audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{secure_filename(audio_file.filename.rsplit('.', 1)[1])}"
                debug_path = os.path.join(debug_dir, debug_filename)
                
                try:
                    import shutil
                    shutil.copy2(temp_file_path, debug_path)
                    logger.warning(f"VOICE API: Saved failed stream audio file for analysis: {debug_path}")
                    
                    # Log raw whisper output for debugging
                    logger.warning(f"VOICE API: Raw Whisper stdout for debugging: {process.stdout}")
                    logger.warning(f"VOICE API: Raw Whisper stderr for debugging: {process.stderr}")
                    
                except Exception as debug_error:
                    logger.warning(f"VOICE API: Could not save debug audio file: {debug_error}")
                
                # Clean up temp files
                try:
                    os.unlink(temp_file_path)
                    if os.path.exists(wav_temp_path):
                        os.unlink(wav_temp_path)
                except (OSError, IOError) as cleanup_error:
                    logger.warning(f"VOICE API: Cleanup error: {cleanup_error}")
                
                logger.error("VOICE API: No speech detected in audio - returning 400 error")
                return jsonify({"error": "No speech detected in audio"}), 400
            
            logger.info(f"Voice API: Successfully transcribed stream audio to text: '{transcribed_text}'")
            
            # Step 2: Return transcription to frontend; frontend sends through normal chat pipeline
            return jsonify({
                "transcribed_text": transcribed_text,
                "session_id": session_id,
                "streaming": True,
                "tts_handled_by": "frontend"
            })
                
        finally:
            # SECURITY FIX: Ensure temporary file is always cleaned up
            try:
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)
                if os.path.exists(wav_temp_path):
                    os.unlink(wav_temp_path)
            except (OSError, IOError) as e:
                logger.warning(f"Failed to clean up temp file {temp_file_path}: {e}")
    
    except subprocess.TimeoutExpired:
        logger.error("Voice API: Stream Whisper.cpp timeout")
        # SECURITY FIX: Clean up temp file on timeout
        try:
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            if 'wav_temp_path' in locals() and os.path.exists(wav_temp_path):
                os.unlink(wav_temp_path)
        except (OSError, IOError):
            pass
        return jsonify({"error": "Speech recognition timeout"}), 500
    except (subprocess.CalledProcessError, OSError, RuntimeError) as e:
        logger.error(f"Voice API: Stream voice chat failed: {e}", exc_info=True)
        # SECURITY FIX: Clean up temp file on error
        try:
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            if 'wav_temp_path' in locals() and os.path.exists(wav_temp_path):
                os.unlink(wav_temp_path)
        except (OSError, IOError):
            pass
        return jsonify({"error": f"Voice chat failed: {str(e)}"}), 500


@voice_bp.route("/install-voice-model", methods=["POST"])
def install_voice_model():
    """
    Install a Piper TTS voice model from HuggingFace.
    Downloads the .onnx and .onnx.json files for the specified voice.

    Request body:
        voice_id: The voice ID to install (e.g., "libritts", "ryan")

    Returns:
        Success message or error
    """
    try:
        data = request.get_json() or {}
        voice_id = data.get("voice_id", "libritts")  # Default to LibriTTS

        if voice_id not in PIPER_VOICES:
            return jsonify({
                "success": False,
                "error": f"Unknown voice ID: {voice_id}",
                "available_voices": list(PIPER_VOICES.keys())
            }), 400

        voice_config = PIPER_VOICES[voice_id]
        model_rel_path = voice_config["model"]
        backend_path = get_backend_path()

        # Create piper-models directory if it doesn't exist
        piper_models_dir = os.path.join(backend_path, "tools/voice/piper-models")
        os.makedirs(piper_models_dir, exist_ok=True)

        model_path = os.path.join(backend_path, model_rel_path)
        config_path = model_path + ".json"

        # Check if already installed
        if os.path.exists(model_path) and os.path.exists(config_path):
            return jsonify({
                "success": True,
                "message": f"Voice model '{voice_config['name']}' is already installed",
                "voice_id": voice_id,
                "already_installed": True
            })

        # Extract voice name components from model path
        # Format: en_US-libritts-high.onnx
        model_filename = os.path.basename(model_rel_path)
        voice_name_full = model_filename.replace(".onnx", "")  # e.g., en_US-libritts-high

        logger.info(f"Voice API: Installing voice model '{voice_name_full}' to {piper_models_dir}")

        # Use piper's download functionality
        try:
            from piper.download_voices import download_voice
            from pathlib import Path

            download_voice(voice_name_full, Path(piper_models_dir))

            # Verify download was successful
            if os.path.exists(model_path) and os.path.exists(config_path):
                model_size = os.path.getsize(model_path)
                logger.info(f"Voice API: Successfully installed voice model '{voice_config['name']}' ({model_size / 1024 / 1024:.1f} MB)")

                return jsonify({
                    "success": True,
                    "message": f"Successfully installed voice model '{voice_config['name']}'",
                    "voice_id": voice_id,
                    "model_size_mb": round(model_size / 1024 / 1024, 1)
                })
            else:
                return jsonify({
                    "success": False,
                    "error": "Download completed but model files not found"
                }), 500

        except ImportError:
            logger.warning("Voice API: piper.download_voices not available, using manual download")

            # Fallback: Manual download from HuggingFace
            import re
            from urllib.request import urlopen
            import shutil

            # Parse voice name: en_US-libritts-high -> lang_family=en, lang_region=US, voice_name=libritts, quality=high
            pattern = re.compile(r"^(?P<lang_family>[^_]+)_(?P<lang_region>[^-]+)-(?P<voice_name>[^-]+)-(?P<voice_quality>.+)$")
            match = pattern.match(voice_name_full)

            if not match:
                return jsonify({
                    "success": False,
                    "error": f"Could not parse voice name: {voice_name_full}"
                }), 400

            lang_family = match.group("lang_family")
            lang_code = f"{lang_family}_{match.group('lang_region')}"
            voice_name = match.group("voice_name")
            voice_quality = match.group("voice_quality")

            url_format = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang_family}/{lang_code}/{voice_name}/{voice_quality}/{lang_code}-{voice_name}-{voice_quality}{extension}?download=true"

            format_args = {
                "lang_family": lang_family,
                "lang_code": lang_code,
                "voice_name": voice_name,
                "voice_quality": voice_quality,
            }

            # Download model file
            model_url = url_format.format(extension=".onnx", **format_args)
            logger.info(f"Voice API: Downloading model from {model_url}")

            with urlopen(model_url) as response:
                with open(model_path, "wb") as model_file:
                    shutil.copyfileobj(response, model_file)

            # Download config file
            config_url = url_format.format(extension=".onnx.json", **format_args)
            logger.info(f"Voice API: Downloading config from {config_url}")

            with urlopen(config_url) as response:
                with open(config_path, "wb") as config_file:
                    shutil.copyfileobj(response, config_file)

            # Verify
            if os.path.exists(model_path) and os.path.exists(config_path):
                model_size = os.path.getsize(model_path)
                logger.info(f"Voice API: Successfully installed voice model '{voice_config['name']}' ({model_size / 1024 / 1024:.1f} MB)")

                return jsonify({
                    "success": True,
                    "message": f"Successfully installed voice model '{voice_config['name']}'",
                    "voice_id": voice_id,
                    "model_size_mb": round(model_size / 1024 / 1024, 1)
                })
            else:
                return jsonify({
                    "success": False,
                    "error": "Download completed but model files not found"
                }), 500

    except Exception as e:
        logger.error(f"Voice API: Failed to install voice model: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": f"Failed to install voice model: {str(e)}"
        }), 500


@voice_bp.route("/voice-models-status", methods=["GET"])
def get_voice_models_status():
    """
    Get detailed status of all voice models including installation status.
    Returns which models are installed and which need to be downloaded.
    """
    try:
        backend_path = get_backend_path()
        piper_models_dir = os.path.join(backend_path, "tools/voice/piper-models")

        models_status = []
        installed_count = 0
        total_count = len(PIPER_VOICES)

        for voice_id, voice_config in PIPER_VOICES.items():
            model_path = os.path.join(backend_path, voice_config["model"])
            config_path = model_path + ".json"

            is_installed = os.path.exists(model_path) and os.path.exists(config_path)
            model_size = os.path.getsize(model_path) if is_installed else 0

            if is_installed:
                installed_count += 1

            models_status.append({
                "voice_id": voice_id,
                "name": voice_config["name"],
                "description": voice_config["description"],
                "installed": is_installed,
                "model_size_mb": round(model_size / 1024 / 1024, 1) if is_installed else None,
                "is_default": voice_id == "libritts"
            })

        # Sort: put default first, then installed, then not installed
        models_status.sort(key=lambda x: (not x["is_default"], not x["installed"], x["name"]))

        return jsonify({
            "success": True,
            "models": models_status,
            "installed_count": installed_count,
            "total_count": total_count,
            "all_installed": installed_count == total_count,
            "piper_models_dir": piper_models_dir,
            "default_voice": "libritts"
        })

    except Exception as e:
        logger.error(f"Voice API: Failed to get voice models status: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@voice_bp.route("/models/download-status", methods=["GET"])
def get_voice_download_status():
    """Get current voice model download progress."""
    try:
        with _voice_download_lock:
            return success_response(_voice_download_status.copy())
    except Exception as e:
        logger.error(f"Error getting voice download status: {e}")
        return error_response(str(e), 500)


@voice_bp.route("/models/download", methods=["POST"])
def download_voice_model():
    """Start downloading a voice model (Piper TTS or Whisper STT) with progress monitoring."""
    global _voice_download_status
    try:
        data = request.get_json() or {}
        model_type = data.get("model_type")  # "piper" or "whisper"
        model_id = data.get("model_id")

        if not model_type or not model_id:
            return error_response("model_type and model_id are required", 400)

        if model_type == "piper" and model_id not in PIPER_VOICES:
            return error_response(f"Unknown Piper voice: {model_id}", 400)
        if model_type == "whisper" and model_id not in WHISPER_MODELS:
            return error_response(f"Unknown Whisper model: {model_id}", 400)

        with _voice_download_lock:
            if _voice_download_status["is_downloading"]:
                return error_response(
                    f"Already downloading: {_voice_download_status['current_model']}", 409
                )

            if model_type == "piper":
                total_mb = PIPER_MODEL_SIZES_MB.get(model_id, 70)
                display_name = PIPER_VOICES.get(model_id, {}).get("name", model_id)
            else:
                total_mb = WHISPER_MODEL_SIZES_MB.get(model_id, 100)
                display_name = WHISPER_MODELS.get(model_id, {}).get("name", model_id)

            _voice_download_status = {
                "is_downloading": True,
                "current_model": model_id,
                "model_type": model_type,
                "progress": 0,
                "status": "starting",
                "error": None,
                "speed_mbps": 0,
                "downloaded_mb": 0,
                "total_mb": total_mb,
            }

        def _download_task():
            global _voice_download_status
            _start_time = time.time()
            backend_path = get_backend_path()
            total_bytes = int(total_mb * 1024 * 1024)

            try:
                with _voice_download_lock:
                    _voice_download_status["status"] = "downloading"

                stop_monitor = threading.Event()

                if model_type == "piper":
                    voice_config = PIPER_VOICES[model_id]
                    model_rel_path = voice_config["model"]
                    target_path = os.path.join(backend_path, model_rel_path)
                    target_dir = os.path.dirname(target_path)
                    os.makedirs(target_dir, exist_ok=True)
                    monitor_paths = [target_path, target_path + ".json"]
                else:
                    model_config = WHISPER_MODELS[model_id]
                    target_path = os.path.join(backend_path, model_config["path"])
                    target_dir = os.path.dirname(target_path)
                    monitor_paths = [target_path]

                def _monitor_progress():
                    while not stop_monitor.is_set():
                        try:
                            downloaded = 0
                            for fpath in monitor_paths:
                                if os.path.exists(fpath):
                                    try:
                                        downloaded += os.path.getsize(fpath)
                                    except OSError:
                                        pass
                            # Also check for partial download files (.tmp, .incomplete)
                            if os.path.isdir(target_dir):
                                for fname in os.listdir(target_dir):
                                    full = os.path.join(target_dir, fname)
                                    if fname.endswith(('.tmp', '.incomplete', '.part')):
                                        try:
                                            downloaded += os.path.getsize(full)
                                        except OSError:
                                            pass

                            elapsed = time.time() - _start_time
                            speed = (downloaded / (1024 * 1024)) / max(elapsed, 0.1)
                            pct = min(int((downloaded / max(total_bytes, 1)) * 100), 99)

                            with _voice_download_lock:
                                _voice_download_status.update({
                                    "progress": pct,
                                    "speed_mbps": round(speed, 1),
                                    "downloaded_mb": round(downloaded / (1024 * 1024), 1),
                                })
                        except Exception:
                            pass
                        stop_monitor.wait(1.0)

                monitor_thread = threading.Thread(target=_monitor_progress, daemon=True)
                monitor_thread.start()

                try:
                    if model_type == "piper":
                        _do_piper_download(backend_path, model_id, PIPER_VOICES[model_id])
                    else:
                        _do_whisper_download(backend_path, model_id, WHISPER_MODELS[model_id])
                finally:
                    stop_monitor.set()
                    monitor_thread.join(timeout=2)

                with _voice_download_lock:
                    _voice_download_status.update({
                        "status": "completed",
                        "progress": 100,
                        "downloaded_mb": total_mb,
                        "total_mb": total_mb,
                    })
                logger.info(f"Voice model downloaded: {model_type}/{model_id}")

            except Exception as e:
                logger.error(f"Voice model download failed: {e}", exc_info=True)
                with _voice_download_lock:
                    _voice_download_status.update({
                        "status": "failed",
                        "error": str(e),
                        "progress": 0,
                    })
            finally:
                with _voice_download_lock:
                    _voice_download_status["is_downloading"] = False

        thread = threading.Thread(target=_download_task, daemon=True)
        thread.start()

        return success_response({
            "message": f"Started downloading {display_name}",
            "status": "downloading",
        })

    except Exception as e:
        logger.error(f"Error starting voice model download: {e}")
        return error_response(str(e), 500)


def _do_piper_download(backend_path, voice_id, voice_config):
    """Download a Piper TTS voice model (called from background thread)."""
    model_rel_path = voice_config["model"]
    piper_models_dir = os.path.join(backend_path, "tools/voice/piper-models")
    os.makedirs(piper_models_dir, exist_ok=True)

    model_path = os.path.join(backend_path, model_rel_path)
    config_path = model_path + ".json"

    if os.path.exists(model_path) and os.path.exists(config_path):
        return  # Already installed

    model_filename = os.path.basename(model_rel_path)
    voice_name_full = model_filename.replace(".onnx", "")

    # Try piper's download_voices first
    try:
        from piper.download_voices import download_voice
        from pathlib import Path
        download_voice(voice_name_full, Path(piper_models_dir))
        if os.path.exists(model_path) and os.path.exists(config_path):
            return
    except ImportError:
        pass

    # Fallback: Manual download from HuggingFace
    import re as _re
    from urllib.request import urlopen
    import shutil as _shutil

    pattern = _re.compile(r"^(?P<lang_family>[^_]+)_(?P<lang_region>[^-]+)-(?P<voice_name>[^-]+)-(?P<voice_quality>.+)$")
    match = pattern.match(voice_name_full)
    if not match:
        raise ValueError(f"Could not parse voice name: {voice_name_full}")

    lang_family = match.group("lang_family")
    lang_code = f"{lang_family}_{match.group('lang_region')}"
    voice_name = match.group("voice_name")
    voice_quality = match.group("voice_quality")

    url_format = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang_family}/{lang_code}/{voice_name}/{voice_quality}/{lang_code}-{voice_name}-{voice_quality}{extension}?download=true"
    format_args = {
        "lang_family": lang_family, "lang_code": lang_code,
        "voice_name": voice_name, "voice_quality": voice_quality,
    }

    model_url = url_format.format(extension=".onnx", **format_args)
    with urlopen(model_url) as response:
        with open(model_path, "wb") as f:
            _shutil.copyfileobj(response, f)

    config_url = url_format.format(extension=".onnx.json", **format_args)
    with urlopen(config_url) as response:
        with open(config_path, "wb") as f:
            _shutil.copyfileobj(response, f)

    if not os.path.exists(model_path):
        raise RuntimeError(f"Download completed but model file not found: {model_path}")


def _do_whisper_download(backend_path, model_id, model_config):
    """Download a Whisper STT model (called from background thread)."""
    model_path = os.path.join(backend_path, model_config["path"])

    if os.path.exists(model_path):
        return  # Already exists

    model_name = os.path.basename(model_config["path"]).replace("ggml-", "").replace(".bin", "")
    download_script = os.path.join(backend_path, "tools/voice/whisper.cpp/models/download-ggml-model.sh")

    if not os.path.exists(download_script):
        raise FileNotFoundError(f"Download script not found: {download_script}")

    result = subprocess.run(
        ["bash", download_script, model_name],
        capture_output=True, text=True, timeout=600, cwd=backend_path
    )

    if result.returncode != 0 or not os.path.exists(model_path):
        raise RuntimeError(f"Failed to download Whisper model {model_name}: {result.stderr}")


@voice_bp.route("/models/all", methods=["GET"])
def list_all_voice_models():
    """List all voice models (Piper TTS + Whisper STT) with installation status."""
    try:
        backend_path = get_backend_path()

        models = []

        # Whisper STT models
        for model_id, model_config in WHISPER_MODELS.items():
            model_path = os.path.join(backend_path, model_config["path"])
            is_installed = os.path.exists(model_path)
            size_mb = os.path.getsize(model_path) / (1024 * 1024) if is_installed else WHISPER_MODEL_SIZES_MB.get(model_id, 0)

            models.append({
                "id": model_id,
                "name": model_config["name"],
                "description": model_config["description"],
                "model_type": "whisper",
                "category": "Speech-to-Text",
                "is_downloaded": is_installed,
                "size_mb": round(size_mb, 1),
            })

        # Piper TTS models
        for voice_id, voice_config in PIPER_VOICES.items():
            model_path = os.path.join(backend_path, voice_config["model"])
            config_path = model_path + ".json"
            is_installed = os.path.exists(model_path) and os.path.exists(config_path)
            size_mb = os.path.getsize(model_path) / (1024 * 1024) if is_installed else PIPER_MODEL_SIZES_MB.get(voice_id, 0)

            models.append({
                "id": voice_id,
                "name": voice_config["name"],
                "description": voice_config["description"],
                "model_type": "piper",
                "category": "Text-to-Speech",
                "is_downloaded": is_installed,
                "size_mb": round(size_mb, 1),
            })

        return success_response({"models": models})

    except Exception as e:
        logger.error(f"Error listing all voice models: {e}", exc_info=True)
        return error_response(str(e), 500)


@voice_bp.route("/install-whisper", methods=["POST"])
def install_whisper():
    """
    Install Whisper.cpp by cloning from GitHub and building from source.
    This enables speech recognition on machines where whisper.cpp is not pre-installed.

    Returns:
        JSON with success status and build details
    """
    try:
        backend_path = get_backend_path()
        whisper_dir = os.path.join(backend_path, "tools/voice/whisper.cpp")
        whisper_cli = os.path.join(whisper_dir, "build/bin/whisper-cli")
        whisper_lib = os.path.join(whisper_dir, "build/src/libwhisper.so.1")

        # Check if already installed and working
        if os.path.exists(whisper_cli):
            try:
                env = os.environ.copy()
                env["LD_LIBRARY_PATH"] = os.path.join(whisper_dir, "build/src")
                result = subprocess.run(
                    [whisper_cli, "--help"],
                    capture_output=True, timeout=10, env=env
                )
                if result.returncode == 0:
                    return jsonify({
                        "success": True,
                        "already_installed": True,
                        "message": "Whisper.cpp is already installed and working"
                    })
            except Exception:
                pass  # Binary exists but doesn't work, proceed with reinstall

        # Check prerequisites
        missing_deps = []
        for dep in ["git", "cmake", "make", "gcc"]:
            if not shutil.which(dep):
                missing_deps.append(dep)

        if missing_deps:
            # Try to auto-install missing build dependencies
            logger.info(f"Voice API: Auto-installing missing deps: {missing_deps}")
            try:
                install_result = subprocess.run(
                    ["sudo", "apt-get", "install", "-y", "cmake", "build-essential"],
                    capture_output=True, text=True, timeout=120
                )
                if install_result.returncode != 0:
                    return jsonify({
                        "success": False,
                        "error": f"Missing build dependencies: {', '.join(missing_deps)}. Auto-install failed. Try: sudo apt install cmake build-essential"
                    }), 400
                # Re-check after install
                still_missing = [dep for dep in ["git", "cmake", "make", "gcc"] if not shutil.which(dep)]
                if still_missing:
                    return jsonify({
                        "success": False,
                        "error": f"Still missing after install: {', '.join(still_missing)}. Try: sudo apt install {' '.join(still_missing)}"
                    }), 400
                logger.info("Voice API: Build dependencies installed successfully")
            except Exception as e:
                return jsonify({
                    "success": False,
                    "error": f"Missing build dependencies: {', '.join(missing_deps)}. Auto-install failed: {str(e)}. Try: sudo apt install cmake build-essential"
                }), 400

        # Remove placeholder directory if it exists but has no source
        if os.path.isdir(whisper_dir):
            has_source = os.path.exists(os.path.join(whisper_dir, "CMakeLists.txt")) or os.path.exists(os.path.join(whisper_dir, "Makefile"))
            if not has_source:
                logger.info("Voice API: Removing whisper.cpp placeholder directory")
                shutil.rmtree(whisper_dir)

        # Clone whisper.cpp
        if not os.path.isdir(whisper_dir):
            logger.info("Voice API: Cloning whisper.cpp from GitHub...")
            clone_result = subprocess.run(
                ["git", "clone", "--depth", "1", "https://github.com/ggerganov/whisper.cpp.git", whisper_dir],
                capture_output=True, text=True, timeout=300
            )
            if clone_result.returncode != 0:
                return jsonify({
                    "success": False,
                    "error": f"Git clone failed: {clone_result.stderr}"
                }), 500

        # Build whisper.cpp
        logger.info("Voice API: Building whisper.cpp...")
        build_dir = os.path.join(whisper_dir, "build")
        os.makedirs(build_dir, exist_ok=True)

        # cmake configure
        cmake_result = subprocess.run(
            ["cmake", "-B", "build"],
            capture_output=True, text=True, timeout=120,
            cwd=whisper_dir
        )
        if cmake_result.returncode != 0:
            return jsonify({
                "success": False,
                "error": f"CMake configure failed: {cmake_result.stderr[-500:]}"
            }), 500

        # cmake build
        build_result = subprocess.run(
            ["cmake", "--build", "build", "--config", "Release", "-j4"],
            capture_output=True, text=True, timeout=600,
            cwd=whisper_dir
        )
        if build_result.returncode != 0:
            return jsonify({
                "success": False,
                "error": f"Build failed: {build_result.stderr[-500:]}"
            }), 500

        # Verify binary exists
        # Check for whisper-cli in various possible locations
        cli_found = False
        for cli_path in [whisper_cli, os.path.join(build_dir, "bin/whisper-cli"), os.path.join(build_dir, "whisper-cli")]:
            if os.path.exists(cli_path):
                cli_found = True
                break

        if not cli_found:
            return jsonify({
                "success": False,
                "error": "Build completed but whisper-cli binary not found. Check build output."
            }), 500

        logger.info("Voice API: Whisper.cpp installed successfully")
        return jsonify({
            "success": True,
            "message": "Whisper.cpp installed and built successfully",
            "whisper_cli": whisper_cli,
            "has_models": len([f for f in os.listdir(os.path.join(whisper_dir, "models")) if f.endswith(".bin")]) > 0 if os.path.isdir(os.path.join(whisper_dir, "models")) else False
        })

    except subprocess.TimeoutExpired:
        logger.error("Voice API: Whisper.cpp installation timed out")
        return jsonify({
            "success": False,
            "error": "Installation timed out. The build may take longer on slower machines."
        }), 500
    except Exception as e:
        logger.error(f"Voice API: Failed to install Whisper.cpp: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": f"Installation failed: {str(e)}"
        }), 500


@voice_bp.route("/install-whisper-model", methods=["POST"])
def install_whisper_model():
    """
    Download a specific Whisper speech recognition model.

    Request body:
        model_id: The model ID to download (e.g., "tiny.en", "base", "small")

    Returns:
        JSON with success status
    """
    try:
        data = request.get_json() or {}
        model_id = data.get("model_id", DEFAULT_WHISPER_MODEL)

        if model_id not in WHISPER_MODELS:
            return jsonify({
                "success": False,
                "error": f"Unknown model ID: {model_id}",
                "available_models": list(WHISPER_MODELS.keys())
            }), 400

        model_config = WHISPER_MODELS[model_id]
        success, model_path, error_msg = ensure_whisper_model_downloaded(model_config)

        if success:
            model_size = os.path.getsize(model_path) if os.path.exists(model_path) else 0
            return jsonify({
                "success": True,
                "message": f"Whisper model '{model_id}' is ready",
                "model_id": model_id,
                "model_size_mb": round(model_size / 1024 / 1024, 1)
            })
        else:
            return jsonify({
                "success": False,
                "error": error_msg or f"Failed to download model '{model_id}'"
            }), 500

    except Exception as e:
        logger.error(f"Voice API: Failed to install whisper model: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": f"Failed to install whisper model: {str(e)}"
        }), 500