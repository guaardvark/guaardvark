"""Tests for thread-local experiment config injection."""
import threading
from backend.utils.experiment_context import (
    set_experiment_config,
    get_experiment_config,
    clear_experiment_config,
)


def test_get_returns_none_by_default():
    clear_experiment_config()
    assert get_experiment_config() is None


def test_set_and_get_config():
    config = {"top_k": 10, "dedup_threshold": 0.75}
    set_experiment_config(config)
    assert get_experiment_config() == config
    clear_experiment_config()


def test_clear_removes_config():
    set_experiment_config({"top_k": 10})
    clear_experiment_config()
    assert get_experiment_config() is None


def test_thread_isolation():
    """Config set in one thread is not visible in another."""
    set_experiment_config({"top_k": 10})
    result = {}

    def check_other_thread():
        result["config"] = get_experiment_config()

    t = threading.Thread(target=check_other_thread)
    t.start()
    t.join()
    assert result["config"] is None
    clear_experiment_config()
