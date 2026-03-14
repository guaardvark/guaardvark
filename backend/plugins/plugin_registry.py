"""
Plugin Registry
Discovers and registers available plugins.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

from .plugin_base import PluginMetadata, PluginStatus, PluginType

logger = logging.getLogger(__name__)


class PluginRegistry:
    """
    Registry for discovering and tracking available plugins.
    
    Scans the plugins directory and maintains a registry of all
    discovered plugins with their metadata.
    """
    
    def __init__(self, plugins_dir: Optional[Path] = None):
        """
        Initialize plugin registry.
        
        Args:
            plugins_dir: Path to plugins directory. Defaults to project root /plugins/
        """
        if plugins_dir is None:
            # Default: project_root/plugins/
            plugins_dir = Path(__file__).parent.parent.parent / 'plugins'
        
        self.plugins_dir = plugins_dir
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
                    self._plugins[metadata.id] = metadata
                    self._plugin_dirs[metadata.id] = item
                    discovered.append(metadata.id)
                    logger.info(f"✓ Discovered plugin: {metadata.id} ({metadata.name}) v{metadata.version}")
                except Exception as e:
                    logger.error(f"✗ Failed to load plugin from {item}: {e}", exc_info=True)
            else:
                logger.debug(f"No plugin.json found in {item.name}")
        
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
    
    def update_plugin_config(self, plugin_id: str, config_updates: Dict[str, Any]) -> bool:
        """
        Update plugin configuration.
        
        Args:
            plugin_id: Plugin ID
            config_updates: Dictionary of config values to update
            
        Returns:
            True if successful
        """
        if plugin_id not in self._plugins:
            logger.warning(f"Plugin not found: {plugin_id}")
            return False
        
        metadata = self._plugins[plugin_id]
        plugin_dir = self._plugin_dirs[plugin_id]
        
        # Update config
        for key, value in config_updates.items():
            if hasattr(metadata.config, key):
                setattr(metadata.config, key, value)
            else:
                metadata.config.extra[key] = value
        
        # Save to plugin.json
        try:
            metadata.save(plugin_dir / 'plugin.json')
            logger.info(f"Updated config for plugin: {plugin_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to save plugin config: {e}")
            return False


# Global registry instance
_registry: Optional[PluginRegistry] = None


def get_plugin_registry() -> PluginRegistry:
    """Get the global plugin registry instance"""
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry
