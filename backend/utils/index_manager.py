import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

__all__ = ["get_or_create_index", "SimpleIndex", "clear_indexes"]


class SimpleIndex:
    def __init__(self, path: str):
        self.path = path
        self.documents: Dict[str, str] = {}

    def add_document(self, doc_id: str, text: str) -> None:
        self.documents[doc_id] = text

    def search(self, term: str) -> List[str]:
        matches = []
        for doc_id, text in self.documents.items():
            if term in text:
                matches.append(doc_id)
        return matches


logger = logging.getLogger(__name__)

_index_cache: Dict[str, SimpleIndex] = {}


def configure_global_settings(llm, embed_model):
    from llama_index.core import Settings

    logger.info("Configuring global LlamaIndex settings.")
    Settings.llm = llm
    Settings.embed_model = embed_model
    Settings.context_window = 8192


from backend.config import INDEX_ROOT, PROJECT_INDEX_MODE


def _index_path(project_id: Optional[str]) -> str:
    root = os.environ.get("GUAARDVARK_INDEX_ROOT", INDEX_ROOT)
    mode = os.environ.get("GUAARDVARK_PROJECT_INDEX_MODE", PROJECT_INDEX_MODE)
    if mode == "per_project":
        pid = str(project_id or "default")
        return str(Path(root) / pid)
    return str(Path(root))


def get_or_create_index(project_id: Optional[str] = None) -> SimpleIndex:
    path = _index_path(project_id)
    Path(path).mkdir(parents=True, exist_ok=True)
    idx = _index_cache.get(path)
    if idx is None:
        idx = SimpleIndex(path)
        _index_cache[path] = idx
    return idx


def clear_indexes() -> None:
    _index_cache.clear()
