
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class PluginStatus(Enum):
    UNKNOWN = "unknown"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"
    DISABLED = "disabled"


class PluginType(Enum):
    SERVICE = "service"
    EXTENSION = "extension"
    TOOL = "tool"
    UI = "ui"


@dataclass
class PluginConfig:
    enabled: bool = False
    auto_start: bool = False
    service_url: Optional[str] = None
    timeout: int = 30
    fallback_enabled: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PluginConfig':
        known_fields = {'enabled', 'auto_start', 'service_url', 'timeout', 'fallback_enabled'}
        known_data = {k: v for k, v in data.items() if k in known_fields}
        extra_data = {k: v for k, v in data.items() if k not in known_fields}
        return cls(**known_data, extra=extra_data)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            'enabled': self.enabled,
            'auto_start': self.auto_start,
            'service_url': self.service_url,
            'timeout': self.timeout,
            'fallback_enabled': self.fallback_enabled,
        }
        result.update(self.extra)
        return result


@dataclass
class PluginMetadata:
    id: str
    name: str
    version: str
    description: str = ""
    author: str = ""
    type: str = "service"
    category: str = "general"
    port: Optional[int] = None
    vram_estimate_mb: int = 0
    core: bool = False
    dependencies: List[str] = field(default_factory=list)
    config: PluginConfig = field(default_factory=PluginConfig)
    requirements: Dict[str, bool] = field(default_factory=dict)
    endpoints: Dict[str, str] = field(default_factory=dict)
    
    @classmethod
    def from_json_file(cls, json_path: Path) -> 'PluginMetadata':
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            config_data = data.pop('config', {})
            config = PluginConfig.from_dict(config_data)
            
            return cls(
                id=data.get('id', json_path.parent.name),
                name=data.get('name', data.get('id', 'Unknown')),
                version=data.get('version', '0.0.0'),
                description=data.get('description', ''),
                author=data.get('author', ''),
                type=data.get('type', 'service'),
                category=data.get('category', 'general'),
                port=data.get('port'),
                vram_estimate_mb=data.get('vram_estimate_mb', 0),
                core=data.get('core', False),
                dependencies=data.get('dependencies', []),
                config=config,
                requirements=data.get('requirements', {}),
                endpoints=data.get('endpoints', {}),
            )
        except Exception as e:
            logger.error(f"Failed to load plugin metadata from {json_path}: {e}")
            raise
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'version': self.version,
            'description': self.description,
            'author': self.author,
            'type': self.type,
            'category': self.category,
            'port': self.port,
            'vram_estimate_mb': self.vram_estimate_mb,
            'core': self.core,
            'dependencies': self.dependencies,
            'config': self.config.to_dict(),
            'requirements': self.requirements,
            'endpoints': self.endpoints,
        }
    
    def save(self, json_path: Path):
        try:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(self.to_dict(), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save plugin metadata to {json_path}: {e}")
            raise


class PluginBase(ABC):
    
    def __init__(self, plugin_dir: Path):
        self.plugin_dir = plugin_dir
        self.metadata: Optional[PluginMetadata] = None
        self.status = PluginStatus.UNKNOWN
        self._load_metadata()
    
    def _load_metadata(self):
        json_path = self.plugin_dir / 'plugin.json'
        if json_path.exists():
            self.metadata = PluginMetadata.from_json_file(json_path)
            if self.metadata.config.enabled:
                self.status = PluginStatus.STOPPED
            else:
                self.status = PluginStatus.DISABLED
    
    @property
    def id(self) -> str:
        return self.metadata.id if self.metadata else self.plugin_dir.name
    
    @property
    def name(self) -> str:
        return self.metadata.name if self.metadata else self.id
    
    @property
    def is_enabled(self) -> bool:
        return self.metadata.config.enabled if self.metadata else False
    
    @property
    def is_running(self) -> bool:
        return self.status == PluginStatus.RUNNING
    
    @abstractmethod
    def start(self) -> bool:
        pass
    
    @abstractmethod
    def stop(self) -> bool:
        pass
    
    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        pass
    
    def enable(self) -> bool:
        if self.metadata:
            self.metadata.config.enabled = True
            self._save_config()
            self.status = PluginStatus.STOPPED
            return True
        return False
    
    def disable(self) -> bool:
        if self.is_running:
            self.stop()
        if self.metadata:
            self.metadata.config.enabled = False
            self._save_config()
            self.status = PluginStatus.DISABLED
            return True
        return False
    
    def _save_config(self):
        if self.metadata:
            json_path = self.plugin_dir / 'plugin.json'
            self.metadata.save(json_path)
    
    def get_info(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'status': self.status.value,
            'enabled': self.is_enabled,
            'running': self.is_running,
            'metadata': self.metadata.to_dict() if self.metadata else None,
            'plugin_dir': str(self.plugin_dir),
        }
