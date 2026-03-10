"""Thread-local experiment config for RAG autoresearch.

During eval runs, the autoresearch orchestrator sets experiment parameters
via set_experiment_config(). The retrieval path (search_with_llamaindex,
_retrieve_rag_context) checks get_experiment_config() and applies overrides
if present. Outside of eval runs, get_experiment_config() returns None and
the default config is used — zero impact on normal user queries.
"""
import threading

_experiment_config = threading.local()


def set_experiment_config(config: dict):
    """Set experiment params for current thread."""
    _experiment_config.params = config


def get_experiment_config():
    """Get experiment params, or None if not in an experiment."""
    return getattr(_experiment_config, "params", None)


def clear_experiment_config():
    """Remove experiment config from current thread."""
    _experiment_config.params = None
