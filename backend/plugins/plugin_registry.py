"""
Plugin Registry
Discovers and registers available plugins.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

from .plugin_base import PluginMetadata, PluginStatus, PluginType
from .plugin_config_store import PluginConfigStore

logger = logging.getLogger(__name__)


class PluginRegistry:
    """
    Registry for discovering and tracking available plugins.
    
    Scans the plugins directory and maintains a registry of all
    discovered plugins with their metadata.
    """
    
    def __init__(
        self,
        plugins_dir: Optional[Path] = None,
        config_store: Optional[PluginConfigStore] = None,
    ):
        """
        Initialize plugin registry.
        
        Args:
            plugins_dir: Path to plugins directory. Defaults to project root /plugins/
            config_store: Per-machine config overlay. Defaults to a store at
                <plugins_dir>.parent/data/plugin_config.json, so tests that pass
                a temp plugins_dir get isolated config for free.
        """
        if plugins_dir is None:
            # Default: project_root/plugins/
            plugins_dir = Path(__file__).parent.parent.parent / 'plugins'
        
        self.plugins_dir = plugins_dir
        if config_store is None:
            config_store = PluginConfigStore(
                Path(plugins_dir).parent / 'data' / 'plugin_config.json'
            )
        self.config_store = config_store
        self._plugins: Dict[str, PluginMetadata] = {}
        self._plugin_dirs: Dict[str, Path] = {}
        
        # Discover plugins on init
        self.discover_plugins()
    
    def discover_plugins(self) -> List[str]:
        """
        Discover all plugins in the plugins directory.
        
        Returns:
            List of discovered plugin IDs
        """
        discovered = []
        
        logger.info(f"Starting plugin discovery in directory: {self.plugins_dir}")
        
        if not self.plugins_dir.exists():
            logger.warning(f"Plugins directory does not exist: {self.plugins_dir}")
            return discovered
        
        logger.debug(f"Plugins directory exists, scanning for plugin.json files...")
        
        for item in self.plugins_dir.iterdir():
            if not item.is_dir():
                logger.debug(f"Skipping non-directory: {item.name}")
                continue
                
            if item.name.startswith('_'):
                logger.debug(f"Skipping hidden directory: {item.name}")
                continue
                
            plugin_json = item / 'plugin.json'
            logger.debug(f"Checking {item.name} for plugin.json: {plugin_json.exists()}")
            
            if plugin_json.exists():
                try:
                    metadata = PluginMetadata.from_json_file(plugin_json)
                    self._apply_overrides(metadata)
                    self._plugins[metadata.id] = metadata
                    self._plugin_dirs[metadata.id] = item
                    discovered.append(metadata.id)
                    logger.info(f"Discovered plugin: {metadata.id} ({metadata.name}) v{metadata.version}")
                except Exception as e:
                    logger.error(f"Failed to load plugin from {item}: {e}", exc_info=True)
            else:
                logger.debug(f"No plugin.json found in {item.name}")
        
        # Sidecars shipped by extensions (extensions/<id>/plugin/plugin.json).
        try:
            from backend import extensions as _ext
            for plugin_dir in _ext.plugin_dirs(_ext.discover()):
                try:
                    metadata = PluginMetadata.from_json_file(plugin_dir / 'plugin.json')
                    self._apply_overrides(metadata)
                    self._plugins[metadata.id] = metadata
                    self._plugin_dirs[metadata.id] = plugin_dir
                    discovered.append(metadata.id)
                    logger.info(f"Discovered extension plugin: {metadata.id} ({metadata.name}) v{metadata.version}")
                except Exception as e:
                    logger.error(f"Failed to load extension plugin from {plugin_dir}: {e}", exc_info=True)
        except Exception as e:
            logger.warning(f"Extension plugin discovery skipped: {e}")

        logger.info(f"Plugin discovery complete: {len(discovered)} plugin(s) discovered")
        if discovered:
            logger.info(f"Discovered plugins: {', '.join(discovered)}")
        return discovered
    
    def refresh(self) -> List[str]:
        """Refresh the plugin registry by re-scanning plugins directory"""
        self._plugins.clear()
        self._plugin_dirs.clear()
        return self.discover_plugins()
    
    def get_plugin(self, plugin_id: str) -> Optional[PluginMetadata]:
        """Get plugin metadata by ID"""
        return self._plugins.get(plugin_id)
    
    def get_plugin_dir(self, plugin_id: str) -> Optional[Path]:
        """Get plugin directory path by ID"""
        return self._plugin_dirs.get(plugin_id)
    
    def get_all_plugins(self) -> Dict[str, PluginMetadata]:
        """Get all registered plugins"""
        return self._plugins.copy()
    
    def list_plugins(self) -> List[Dict[str, Any]]:
        """
        List all plugins with their basic info.
        
        Returns:
            List of plugin info dictionaries
        """
        result = []
        for plugin_id, metadata in self._plugins.items():
            result.append({
                'id': plugin_id,
                'name': metadata.name,
                'version': metadata.version,
                'description': metadata.description,
                'type': metadata.type,
                'category': metadata.category,
                'enabled': metadata.config.enabled,
                'port': metadata.port,
                'vram_estimate_mb': metadata.vram_estimate_mb,
                'plugin_dir': str(self._plugin_dirs.get(plugin_id, '')),
                'config': metadata.config.to_dict(),
            })
        return result
    
    def get_plugins_by_type(self, plugin_type: str) -> List[PluginMetadata]:
        """Get all plugins of a specific type"""
        return [
            meta for meta in self._plugins.values()
            if meta.type == plugin_type
        ]
    
    def get_plugins_by_category(self, category: str) -> List[PluginMetadata]:
        """Get all plugins in a specific category"""
        return [
            meta for meta in self._plugins.values()
            if meta.category == category
        ]
    
    def get_enabled_plugins(self) -> List[PluginMetadata]:
        """Get all enabled plugins"""
        return [
            meta for meta in self._plugins.values()
            if meta.config.enabled
        ]
    
    def is_registered(self, plugin_id: str) -> bool:
        """Check if a plugin is registered"""
        return plugin_id in self._plugins
    
    # Keys that represent per-machine runtime state. These belong in
    # data/plugin_state.json (user_enabled overlay), NOT in plugin.json.
    _RUNTIME_STATE_KEYS = frozenset({"enabled", "auto_start"})

    def update_plugin_config(self, plugin_id: str, config_updates: Dict[str, Any]) -> bool:
        """
        Update plugin manifest configuration on disk.

        Refuses any update that touches runtime-state keys (enabled, auto_start) —
        those must go through PluginManager.enable_plugin/disable_plugin which
        writes to data/plugin_state.json instead. This prevents per-machine
        plugin.json drift between client and master nodes.

        Args:
            plugin_id: Plugin ID
            config_updates: Dictionary of config values to update

        Returns:
            True if successful, False if rejected or failed
        """
        if plugin_id not in self._plugins:
            logger.warning(f"Plugin not found: {plugin_id}")
            return False

        forbidden = self._RUNTIME_STATE_KEYS.intersection(config_updates.keys())
        if forbidden:
            logger.error(
                f"Refusing update_plugin_config for {plugin_id}: "
                f"runtime-state keys {sorted(forbidden)} must use "
                f"PluginManager.enable_plugin/disable_plugin (writes "
                f"data/plugin_state.json), not plugin.json."
            )
            return False

        metadata = self._plugins[plugin_id]

        # The config dialog posts the whole config object back, which includes
        # the manifest's default_* keys. They are not runtime-state keys, so the
        # guard above lets them through — but PluginConfig has no such
        # attributes, so they would land in `extra` and then shadow the real
        # fields via to_dict()'s `result.update(self.extra)`. Drop them here.
        updates = {
            k: v
            for k, v in config_updates.items()
            if k not in ('default_enabled', 'default_auto_start')
        }
        if not updates:
            return True

        try:
            # Per-machine overlay — never plugins/<id>/plugin.json, which is
            # tracked in git and forked into customer projects.
            self.config_store.update(plugin_id, updates)
        except Exception as e:
            logger.error(f"Failed to save plugin config overlay: {e}")
            return False

        # Reflect it in the live metadata so the running process sees it now.
        self._assign_config(metadata, updates)
        logger.info(f"Updated config overlay for plugin: {plugin_id}")
        return True

    def _assign_config(self, metadata: PluginMetadata, values: Dict[str, Any]) -> None:
        for key, value in values.items():
            if hasattr(metadata.config, key):
                setattr(metadata.config, key, value)
            else:
                metadata.config.extra[key] = value

    def _apply_overrides(self, metadata: PluginMetadata) -> None:
        """Layer this machine's saved settings over the shipped manifest."""
        try:
            overrides = self.config_store.get(metadata.id)
        except Exception as e:
            logger.warning(f"Could not read config overrides for {metadata.id}: {e}")
            return
        if overrides:
            self._assign_config(metadata, overrides)


# Global registry instance
_registry: Optional[PluginRegistry] = None


def get_plugin_registry() -> PluginRegistry:
    """Get the global plugin registry instance"""
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry
