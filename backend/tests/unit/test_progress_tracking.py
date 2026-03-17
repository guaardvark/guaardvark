# backend/tests/unit/test_progress_tracking.py
# Unit tests for progress tracking system

import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from backend.utils.progress_emitter import (
    ProgressTracker,
    create_progress_tracker,
    update_progress,
    complete_progress,
    error_progress,
    emit_progress_event,
)


class TestProgressEmitter:
    """Test progress emitter functionality"""

    def test_create_progress_tracker(self):
        """Test creating a progress tracker"""
        with patch("backend.utils.progress_emitter._get_socketio") as mock_socketio:
            mock_socketio.return_value = Mock()

            process_id = create_progress_tracker("test", "Test process")

            # Should return a process ID
            assert process_id is not None
            assert process_id.startswith("test_")

    def test_update_progress(self):
        """Test updating progress"""
        with patch("backend.utils.progress_emitter._get_socketio") as mock_socketio:
            mock_socket = Mock()
            mock_socketio.return_value = mock_socket

            process_id = "test_123"
            update_progress(process_id, 50, "Halfway done", "test")

            # Should call emit on the socket
            mock_socket.emit.assert_called()

    def test_complete_progress(self):
        """Test completing progress"""
        with patch("backend.utils.progress_emitter._get_socketio") as mock_socketio:
            mock_socket = Mock()
            mock_socketio.return_value = mock_socket

            process_id = "test_123"
            complete_progress(process_id, "All done", "test")

            # Should call emit on the socket
            mock_socket.emit.assert_called()

    def test_error_progress(self):
        """Test error progress"""
        with patch("backend.utils.progress_emitter._get_socketio") as mock_socketio:
            mock_socket = Mock()
            mock_socketio.return_value = mock_socket

            process_id = "test_123"
            error_progress(process_id, "Something went wrong", "test")

            # Should call emit on the socket
            mock_socket.emit.assert_called()

    def test_emit_progress_event(self):
        """Test emitting progress event"""
        with patch("backend.utils.progress_emitter._get_socketio") as mock_socketio:
            mock_socket = Mock()
            mock_socketio.return_value = mock_socket

            emit_progress_event("test_123", 75, "Almost done", "processing", "test")

            # Should call emit twice (to specific room and global)
            assert mock_socket.emit.call_count == 2

            # Check the call arguments
            calls = mock_socket.emit.call_args_list
            assert calls[0][0][0] == "job_progress"  # event name
            assert calls[0][1]["to"] == "test_123"  # specific room
            assert calls[1][1]["to"] == "global_progress"  # global room

    def test_progress_tracker_context_manager(self):
        """Test ProgressTracker context manager"""
        with patch("backend.utils.progress_emitter._get_socketio") as mock_socketio:
            mock_socket = Mock()
            mock_socketio.return_value = mock_socket

            with ProgressTracker("test", "Test context") as progress:
                # Should have a process_id
                assert progress.process_id is not None
                assert progress.process_id.startswith("test_")

                # Should be able to update progress
                progress.update(50, "Halfway")
                progress.update(100, "Done")

            # Should have emitted events
            assert mock_socket.emit.call_count > 0

    def test_progress_tracker_context_manager_error(self):
        """Test ProgressTracker context manager with error"""
        with patch("backend.utils.progress_emitter._get_socketio") as mock_socketio:
            mock_socket = Mock()
            mock_socketio.return_value = mock_socket

            with pytest.raises(ValueError):
                with ProgressTracker("test", "Test error") as progress:
                    progress.update(50, "Halfway")
                    raise ValueError("Test error")

            # Should have emitted error event
            assert mock_socket.emit.call_count > 0

    def test_socketio_unavailable(self):
        """Test behavior when SocketIO is not available"""
        with patch("backend.utils.progress_emitter._get_socketio") as mock_socketio:
            mock_socketio.return_value = None

            # Should not raise error even if SocketIO is unavailable
            process_id = create_progress_tracker("test", "Test process")
            update_progress(process_id, 50, "Halfway done", "test")
            complete_progress(process_id, "All done", "test")

            # Should complete without errors
            assert process_id is not None


class TestProgressIntegration:
    """Integration tests for progress tracking"""

    def test_multiple_concurrent_processes(self):
        """Test multiple concurrent processes"""
        with patch("backend.utils.progress_emitter._get_socketio") as mock_socketio:
            mock_socket = Mock()
            mock_socketio.return_value = mock_socket

            # Create multiple processes
            process1 = create_progress_tracker("indexing", "Doc 1")
            process2 = create_progress_tracker("file_generation", "File 1")
            process3 = create_progress_tracker("llm_processing", "Chat 1")

            # Update them concurrently
            update_progress(process1, 25, "Indexing 25%", "indexing")
            update_progress(process2, 50, "Generating 50%", "file_generation")
            update_progress(process3, 75, "Processing 75%", "llm_processing")

            # Complete them
            complete_progress(process1, "Indexing done", "indexing")
            complete_progress(process2, "File generated", "file_generation")
            complete_progress(process3, "LLM done", "llm_processing")

            # Should have emitted many events
            assert mock_socket.emit.call_count > 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
