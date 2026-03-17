# backend/tests/unit/test_unified_progress_system.py
# Comprehensive unit tests for the unified progress system

import pytest
import time
import threading
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

from backend.utils.unified_progress_system import (
    UnifiedProgressSystem,
    ProgressEvent,
    ProcessStatus,
    ProcessType,
    get_unified_progress,
    create_progress_tracker,
    update_progress,
    complete_progress,
    error_progress,
    ProgressTracker,
)


class TestProgressEvent:
    """Test ProgressEvent class"""

    def test_progress_event_creation(self):
        """Test creating a progress event"""
        event = ProgressEvent(
            process_id="test_123",
            progress=50,
            message="Halfway done",
            status=ProcessStatus.PROCESSING,
            process_type=ProcessType.INDEXING,
            additional_data={"key": "value"},
        )

        assert event.process_id == "test_123"
        assert event.progress == 50
        assert event.message == "Halfway done"
        assert event.status == ProcessStatus.PROCESSING
        assert event.process_type == ProcessType.INDEXING
        assert event.additional_data == {"key": "value"}

    def test_progress_bounds(self):
        """Test progress bounds enforcement"""
        event = ProgressEvent(
            process_id="test",
            progress=150,  # Should be clamped to 100
            message="Test",
            status=ProcessStatus.PROCESSING,
            process_type=ProcessType.INDEXING,
        )

        assert event.progress == 100

        event = ProgressEvent(
            process_id="test",
            progress=-10,  # Should be clamped to 0
            message="Test",
            status=ProcessStatus.PROCESSING,
            process_type=ProcessType.INDEXING,
        )

        assert event.progress == 0

    def test_to_dict(self):
        """Test converting event to dictionary"""
        event = ProgressEvent(
            process_id="test_123",
            progress=75,
            message="Test message",
            status=ProcessStatus.COMPLETE,
            process_type=ProcessType.FILE_GENERATION,
            additional_data={"custom": "data"},
        )

        event_dict = event.to_dict()

        assert event_dict["job_id"] == "test_123"
        assert event_dict["progress"] == 75
        assert event_dict["message"] == "Test message"
        assert event_dict["status"] == "complete"
        assert event_dict["process_type"] == "file_generation"
        assert event_dict["custom"] == "data"
        assert "timestamp" in event_dict


class TestUnifiedProgressSystem:
    """Test UnifiedProgressSystem class"""

    def setup_method(self):
        """Set up test environment"""
        self.progress_system = UnifiedProgressSystem()
        self.progress_system.initialize(output_dir="/tmp/test_progress")

    def test_initialize(self):
        """Test system initialization"""
        system = UnifiedProgressSystem()
        system.initialize(output_dir="/tmp/test_progress", socketio=Mock())

        assert system._output_dir == "/tmp/test_progress"
        assert system._socketio is not None

    def test_create_process(self):
        """Test creating a new process"""
        with patch.object(self.progress_system, "_emit_event") as mock_emit:
            process_id = self.progress_system.create_process(
                ProcessType.INDEXING, "Test indexing"
            )

            assert process_id.startswith("indexing_")
            assert len(process_id) == len("indexing_") + 8

            # Check that process was added to active processes
            assert process_id in self.progress_system._active_processes

            # Check that event was emitted
            mock_emit.assert_called_once()

    def test_update_process(self):
        """Test updating a process"""
        # Create a process first
        process_id = self.progress_system.create_process(
            ProcessType.INDEXING, "Test indexing"
        )

        with patch.object(self.progress_system, "_emit_event") as mock_emit:
            success = self.progress_system.update_process(
                process_id, 50, "Halfway done"
            )

            assert success is True

            # Check that process was updated
            process = self.progress_system._active_processes[process_id]
            assert process.progress == 50
            assert process.message == "Halfway done"
            assert process.status == ProcessStatus.PROCESSING

            # Check that event was emitted
            mock_emit.assert_called_once()

    def test_update_nonexistent_process(self):
        """Test updating a non-existent process"""
        success = self.progress_system.update_process("nonexistent_id", 50, "Test")

        assert success is False

    def test_complete_process(self):
        """Test completing a process"""
        # Create a process first
        process_id = self.progress_system.create_process(
            ProcessType.INDEXING, "Test indexing"
        )

        with patch.object(self.progress_system, "_emit_event") as mock_emit:
            success = self.progress_system.complete_process(
                process_id, "Indexing complete"
            )

            assert success is True

            # Check that process was completed
            process = self.progress_system._active_processes[process_id]
            assert process.progress == 100
            assert process.message == "Indexing complete"
            assert process.status == ProcessStatus.COMPLETE

            # Check that event was emitted
            mock_emit.assert_called_once()

    def test_error_process(self):
        """Test erroring a process"""
        # Create a process first
        process_id = self.progress_system.create_process(
            ProcessType.INDEXING, "Test indexing"
        )

        with patch.object(self.progress_system, "_emit_event") as mock_emit:
            success = self.progress_system.error_process(process_id, "Indexing failed")

            assert success is True

            # Check that process was marked as error
            process = self.progress_system._active_processes[process_id]
            assert process.message == "Indexing failed"
            assert process.status == ProcessStatus.ERROR

            # Check that event was emitted
            mock_emit.assert_called_once()

    def test_cancel_process(self):
        """Test cancelling a process"""
        # Create a process first
        process_id = self.progress_system.create_process(
            ProcessType.INDEXING, "Test indexing"
        )

        with patch.object(self.progress_system, "_emit_event") as mock_emit:
            success = self.progress_system.cancel_process(
                process_id, "Indexing cancelled"
            )

            assert success is True

            # Check that process was marked as cancelled
            process = self.progress_system._active_processes[process_id]
            assert process.message == "Indexing cancelled"
            assert process.status == ProcessStatus.CANCELLED

            # Check that event was emitted
            mock_emit.assert_called_once()

    def test_get_active_processes(self):
        """Test getting active processes"""
        # Create multiple processes
        process1 = self.progress_system.create_process(
            ProcessType.INDEXING, "Process 1"
        )
        process2 = self.progress_system.create_process(
            ProcessType.FILE_GENERATION, "Process 2"
        )

        active_processes = self.progress_system.get_active_processes()

        assert len(active_processes) == 2
        assert process1 in active_processes
        assert process2 in active_processes

    def test_get_process(self):
        """Test getting a specific process"""
        process_id = self.progress_system.create_process(
            ProcessType.INDEXING, "Test indexing"
        )

        process = self.progress_system.get_process(process_id)

        assert process is not None
        assert process.process_id == process_id
        assert process.process_type == ProcessType.INDEXING

    def test_get_nonexistent_process(self):
        """Test getting a non-existent process"""
        process = self.progress_system.get_process("nonexistent_id")

        assert process is None

    def test_get_processes_by_type(self):
        """Test getting processes by type"""
        # Create processes of different types
        self.progress_system.create_process(ProcessType.INDEXING, "Index 1")
        self.progress_system.create_process(ProcessType.INDEXING, "Index 2")
        self.progress_system.create_process(ProcessType.FILE_GENERATION, "File 1")

        indexing_processes = self.progress_system.get_processes_by_type(
            ProcessType.INDEXING
        )

        assert len(indexing_processes) == 2
        for process in indexing_processes:
            assert process.process_type == ProcessType.INDEXING

    def test_listener_management(self):
        """Test adding and removing listeners"""
        listener = Mock()

        # Add listener
        self.progress_system.add_listener(listener)
        assert listener in self.progress_system._listeners

        # Remove listener
        self.progress_system.remove_listener(listener)
        assert listener not in self.progress_system._listeners

    def test_emit_event_to_listeners(self):
        """Test that events are emitted to listeners"""
        listener = Mock()
        self.progress_system.add_listener(listener)

        event = ProgressEvent(
            process_id="test",
            progress=50,
            message="Test",
            status=ProcessStatus.PROCESSING,
            process_type=ProcessType.INDEXING,
        )

        with patch.object(self.progress_system, "_get_socketio") as mock_socketio:
            mock_socketio.return_value = None
            self.progress_system._emit_event(event)

            listener.assert_called_once_with(event)

    def test_emit_event_socketio_error_handling(self):
        """Test SocketIO error handling during event emission"""
        event = ProgressEvent(
            process_id="test",
            progress=50,
            message="Test",
            status=ProcessStatus.PROCESSING,
            process_type=ProcessType.INDEXING,
        )

        mock_socketio = Mock()
        mock_socketio.emit.side_effect = Exception("SocketIO error")

        with patch.object(self.progress_system, "_get_socketio") as mock_get_socketio:
            mock_get_socketio.return_value = mock_socketio

            # Should not raise exception
            self.progress_system._emit_event(event)

    def test_get_global_progress_no_processes(self):
        """Test global progress with no active processes"""
        global_progress = self.progress_system.get_global_progress()

        assert global_progress["active"] is False
        assert global_progress["progress"] == 0
        assert global_progress["message"] == ""
        assert global_progress["process_count"] == 0

    def test_get_global_progress_with_processes(self):
        """Test global progress with active processes"""
        # Create processes with different progress values
        process1 = self.progress_system.create_process(
            ProcessType.INDEXING, "Process 1"
        )
        process2 = self.progress_system.create_process(
            ProcessType.FILE_GENERATION, "Process 2"
        )

        self.progress_system.update_process(process1, 50, "Halfway")
        self.progress_system.update_process(process2, 100, "Complete")

        global_progress = self.progress_system.get_global_progress()

        assert global_progress["active"] is True
        assert global_progress["progress"] == 75  # Average of 50 and 100
        assert global_progress["process_count"] == 2

    def test_cleanup_process(self):
        """Test automatic cleanup of completed processes"""
        process_id = self.progress_system.create_process(
            ProcessType.INDEXING, "Test indexing"
        )

        # Complete the process
        self.progress_system.complete_process(process_id, "Complete")

        # Process should still be in active processes initially
        assert process_id in self.progress_system._active_processes

        # Trigger cleanup
        self.progress_system._cleanup_process(process_id)

        # Process should be removed
        assert process_id not in self.progress_system._active_processes


class TestBackwardCompatibility:
    """Test backward compatibility functions"""

    def test_create_progress_tracker(self):
        """Test backward compatibility create_progress_tracker"""
        with patch(
            "backend.utils.unified_progress_system._unified_progress"
        ) as mock_system:
            mock_system.create_process.return_value = "test_id"

            process_id = create_progress_tracker("indexing", "Test indexing")

            assert process_id == "test_id"
            mock_system.create_process.assert_called_once()

    def test_update_progress(self):
        """Test backward compatibility update_progress"""
        with patch(
            "backend.utils.unified_progress_system._unified_progress"
        ) as mock_system:
            mock_system.update_process.return_value = True

            success = update_progress("test_id", 50, "Halfway", "indexing")

            assert success is True
            mock_system.update_process.assert_called_once_with("test_id", 50, "Halfway")

    def test_complete_progress(self):
        """Test backward compatibility complete_progress"""
        with patch(
            "backend.utils.unified_progress_system._unified_progress"
        ) as mock_system:
            mock_system.complete_process.return_value = True

            success = complete_progress("test_id", "Complete", "indexing")

            assert success is True
            mock_system.complete_process.assert_called_once_with("test_id", "Complete")

    def test_error_progress(self):
        """Test backward compatibility error_progress"""
        with patch(
            "backend.utils.unified_progress_system._unified_progress"
        ) as mock_system:
            mock_system.error_process.return_value = True

            success = error_progress("test_id", "Error", "indexing")

            assert success is True
            mock_system.error_process.assert_called_once_with("test_id", "Error")


class TestProgressTracker:
    """Test ProgressTracker context manager"""

    def test_progress_tracker_context_manager_success(self):
        """Test ProgressTracker context manager with successful execution"""
        with patch(
            "backend.utils.unified_progress_system._unified_progress"
        ) as mock_system:
            mock_system.create_process.return_value = "test_id"

            with ProgressTracker(ProcessType.INDEXING, "Test indexing") as progress:
                assert progress.process_id == "test_id"
                progress.update(50, "Halfway")
                progress.complete("Done")

            # Should have called complete_process
            mock_system.complete_process.assert_called_with("test_id", "Complete")

    def test_progress_tracker_context_manager_error(self):
        """Test ProgressTracker context manager with error"""
        with patch(
            "backend.utils.unified_progress_system._unified_progress"
        ) as mock_system:
            mock_system.create_process.return_value = "test_id"

            with pytest.raises(ValueError):
                with ProgressTracker(ProcessType.INDEXING, "Test indexing") as progress:
                    progress.update(50, "Halfway")
                    raise ValueError("Test error")

            # Should have called error_process
            mock_system.error_process.assert_called_with("test_id", "Error: Test error")


class TestConcurrency:
    """Test concurrent access to the progress system"""

    def test_concurrent_process_creation(self):
        """Test creating processes concurrently"""
        system = UnifiedProgressSystem()
        system.initialize()

        def create_process(process_id):
            return system.create_process(ProcessType.INDEXING, f"Process {process_id}")

        # Create processes in multiple threads
        threads = []
        results = []

        for i in range(10):
            thread = threading.Thread(
                target=lambda i=i: results.append(create_process(i))
            )
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # All processes should be created successfully
        assert len(results) == 10
        assert len(system._active_processes) == 10

    def test_concurrent_updates(self):
        """Test updating processes concurrently"""
        system = UnifiedProgressSystem()
        system.initialize()

        # Create a process
        process_id = system.create_process(ProcessType.INDEXING, "Test")

        def update_process(thread_id):
            for i in range(10):
                system.update_process(
                    process_id, i * 10, f"Update {i} from thread {thread_id}"
                )
                time.sleep(0.01)

        # Update the process in multiple threads
        threads = []
        for i in range(5):
            thread = threading.Thread(target=lambda i=i: update_process(i))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Process should still exist and be in a valid state
        assert process_id in system._active_processes
        process = system._active_processes[process_id]
        assert 0 <= process.progress <= 100


class TestGlobalInstance:
    """Test global unified progress instance"""

    def test_get_unified_progress(self):
        """Test getting the global unified progress instance"""
        instance1 = get_unified_progress()
        instance2 = get_unified_progress()

        # Should return the same instance
        assert instance1 is instance2
        assert isinstance(instance1, UnifiedProgressSystem)
