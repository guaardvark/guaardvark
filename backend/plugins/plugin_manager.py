"""
Plugin Manager
Manages plugin lifecycle and operations.
"""

import json
import logging
import os
import subprocess
import time
import requests
from pathlib import Path
from typing import Dict, List, Optional, Any

from .plugin_base import PluginStatus, PluginMetadata
from .plugin_registry import PluginRegistry, get_plugin_registry

logger = logging.getLogger(__name__)

# Where we save which plugins were running (survives reboots)
GUAARDVARK_ROOT = os.environ.get("GUAARDVARK_ROOT", str(Path(__file__).resolve().parents[2]))
PLUGIN_STATE_FILE = os.path.join(GUAARDVARK_ROOT, "data", "plugin_state.json")


class PluginManager:
    """
    Manages plugin lifecycle operations.
    
    Handles starting, stopping, and monitoring plugins.
    Works with the PluginRegistry for plugin discovery.
    """
    
    def __init__(self, registry: Optional[PluginRegistry] = None):
        """
        Initialize plugin manager.
        
        Args:
            registry: Plugin registry instance. Uses global if not provided.
        """
        self.registry = registry or get_plugin_registry()
        self._plugin_status: Dict[str, PluginStatus] = {}
        self._plugin_pids: Dict[str, int] = {}
        
        # Initialize status for all plugins
        self._init_plugin_status()
    
    def _init_plugin_status(self):
        """Initialize plugin status and restore previously running plugins."""
        # First pass: detect what's already running
        for plugin_id, metadata in self.registry.get_all_plugins().items():
            if metadata.config.enabled:
                if self._check_service_running(metadata):
                    self._plugin_status[plugin_id] = PluginStatus.RUNNING
                else:
                    self._plugin_status[plugin_id] = PluginStatus.STOPPED
            else:
                self._plugin_status[plugin_id] = PluginStatus.DISABLED

        # Second pass: start plugins that were running last time
        saved = self._load_state()
        for plugin_id in saved.get("running", []):
            if self._plugin_status.get(plugin_id) == PluginStatus.STOPPED:
                logger.info(f"Restoring plugin: {plugin_id} (was running before shutdown)")
                try:
                    result = self.start_plugin(plugin_id)
                    if result.get('success'):
                        logger.info(f"Restored plugin: {plugin_id}")
                    else:
                        logger.warning(f"Failed to restore {plugin_id}: {result.get('error', 'unknown')}")
                except Exception as e:
                    logger.warning(f"Error restoring {plugin_id}: {e}")

    def _save_state(self):
        """Save which plugins are currently running to disk."""
        running = [pid for pid, status in self._plugin_status.items()
                   if status == PluginStatus.RUNNING]
        try:
            os.makedirs(os.path.dirname(PLUGIN_STATE_FILE), exist_ok=True)
            with open(PLUGIN_STATE_FILE, 'w') as f:
                json.dump({"running": running}, f)
        except Exception as e:
            logger.warning(f"Could not save plugin state: {e}")

    def _load_state(self) -> dict:
        """Load saved plugin state from disk."""
        try:
            if os.path.exists(PLUGIN_STATE_FILE):
                with open(PLUGIN_STATE_FILE) as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load plugin state: {e}")
        return {"running": []}
    
    def _check_service_running(self, metadata: PluginMetadata) -> bool:
        """Check if a service plugin is running by hitting its health endpoint"""
        if metadata.type != 'service':
            return False
        
        health_endpoint = metadata.endpoints.get('health', '/health')
        service_url = metadata.config.service_url
        
        if not service_url:
            if metadata.port:
                service_url = f"http://localhost:{metadata.port}"
            else:
                return False
        
        try:
            url = f"{service_url.rstrip('/')}{health_endpoint}"
            response = requests.get(url, timeout=2)
            return response.status_code == 200
        except Exception:
            return False
    
    def get_status(self, plugin_id: str) -> PluginStatus:
        """Get current status of a plugin"""
        if plugin_id not in self._plugin_status:
            return PluginStatus.UNKNOWN
        return self._plugin_status[plugin_id]
    
    def get_all_status(self) -> Dict[str, str]:
        """Get status of all plugins"""
        # Refresh status before returning
        self._refresh_status()
        return {pid: status.value for pid, status in self._plugin_status.items()}
    
    def _refresh_status(self):
        """Refresh status of all plugins"""
        for plugin_id, metadata in self.registry.get_all_plugins().items():
            if not metadata.config.enabled:
                self._plugin_status[plugin_id] = PluginStatus.DISABLED
            elif metadata.type == 'service':
                if self._check_service_running(metadata):
                    self._plugin_status[plugin_id] = PluginStatus.RUNNING
                else:
                    self._plugin_status[plugin_id] = PluginStatus.STOPPED
    
    def start_plugin(self, plugin_id: str) -> Dict[str, Any]:
        """
        Start a plugin.
        
        Args:
            plugin_id: Plugin ID to start
            
        Returns:
            Result dictionary with status and message
        """
        metadata = self.registry.get_plugin(plugin_id)
        if not metadata:
            return {'success': False, 'error': f'Plugin not found: {plugin_id}'}
        
        if not metadata.config.enabled:
            return {'success': False, 'error': 'Plugin is disabled. Enable it first.'}
        
        if self._plugin_status.get(plugin_id) == PluginStatus.RUNNING:
            return {'success': True, 'message': 'Plugin already running'}

        # Check dependencies are running
        for dep_id in getattr(metadata, 'dependencies', []):
            if not self.registry.is_registered(dep_id):
                return {
                    'success': False,
                    'error': f"Required dependency '{dep_id}' is not installed"
                }
            dep_status = self.get_status(dep_id)
            if dep_status != PluginStatus.RUNNING:
                return {
                    'success': False,
                    'error': f"Required dependency '{dep_id}' is not running (status: {dep_status.value})"
                }

        plugin_dir = self.registry.get_plugin_dir(plugin_id)
        if not plugin_dir:
            return {'success': False, 'error': 'Plugin directory not found'}
        
        # Set status to starting
        self._plugin_status[plugin_id] = PluginStatus.STARTING
        
        # Try to find and run start script
        start_script = plugin_dir / 'scripts' / 'start.sh'
        if start_script.exists():
            try:
                result = subprocess.run(
                    ['bash', str(start_script)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=str(plugin_dir)
                )
                
                if result.returncode != 0:
                    self._plugin_status[plugin_id] = PluginStatus.ERROR
                    logger.error(f"Failed to start plugin {plugin_id}: {result.stderr}")
                    return {
                        'success': False,
                        'error': f'Start script failed: {result.stderr}'
                    }
                
                # Wait for service to become healthy (retry loop)
                max_retries = 20
                for i in range(max_retries):
                    if self._check_service_running(metadata):
                        self._plugin_status[plugin_id] = PluginStatus.RUNNING
                        self._save_state()
                        logger.info(f"Plugin started: {plugin_id}")
                        return {
                            'success': True,
                            'message': 'Plugin started successfully',
                            'output': result.stdout
                        }
                    time.sleep(0.5)
                
                # Check if process is still running
                # (Simple check: if we can't connect after retries, assume failure)
                self._plugin_status[plugin_id] = PluginStatus.ERROR
                return {
                    'success': False,
                    'error': 'Plugin started but health check failed (timeout)'
                }
                    
            except subprocess.TimeoutExpired:
                self._plugin_status[plugin_id] = PluginStatus.ERROR
                return {'success': False, 'error': 'Start script timed out'}
            except Exception as e:
                self._plugin_status[plugin_id] = PluginStatus.ERROR
                logger.error(f"Error starting plugin {plugin_id}: {e}")
                return {'success': False, 'error': str(e)}
        else:
            self._plugin_status[plugin_id] = PluginStatus.ERROR
            return {'success': False, 'error': 'No start script found'}
    
    def stop_plugin(self, plugin_id: str) -> Dict[str, Any]:
        """
        Stop a plugin.
        
        Args:
            plugin_id: Plugin ID to stop
            
        Returns:
            Result dictionary with status and message
        """
        metadata = self.registry.get_plugin(plugin_id)
        if not metadata:
            return {'success': False, 'error': f'Plugin not found: {plugin_id}'}
        
        current_status = self._plugin_status.get(plugin_id)
        if current_status == PluginStatus.STOPPED:
            return {'success': True, 'message': 'Plugin already stopped'}
        
        if current_status == PluginStatus.DISABLED:
            return {'success': True, 'message': 'Plugin is disabled'}
        
        plugin_dir = self.registry.get_plugin_dir(plugin_id)
        if not plugin_dir:
            return {'success': False, 'error': 'Plugin directory not found'}
        
        # Set status to stopping
        self._plugin_status[plugin_id] = PluginStatus.STOPPING
        
        # Try to find and run stop script
        stop_script = plugin_dir / 'scripts' / 'stop.sh'
        if stop_script.exists():
            try:
                result = subprocess.run(
                    ['bash', str(stop_script)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=str(plugin_dir)
                )
                
                # Even if stop script has issues, check if service is actually stopped
                time.sleep(1)
                
                if not self._check_service_running(metadata):
                    self._plugin_status[plugin_id] = PluginStatus.STOPPED
                    self._save_state()
                    logger.info(f"Plugin stopped: {plugin_id}")
                    return {
                        'success': True,
                        'message': 'Plugin stopped successfully',
                        'output': result.stdout
                    }
                else:
                    self._plugin_status[plugin_id] = PluginStatus.RUNNING
                    return {
                        'success': False,
                        'error': 'Stop script ran but service still running'
                    }
                    
            except subprocess.TimeoutExpired:
                return {'success': False, 'error': 'Stop script timed out'}
            except Exception as e:
                logger.error(f"Error stopping plugin {plugin_id}: {e}")
                return {'success': False, 'error': str(e)}
        else:
            return {'success': False, 'error': 'No stop script found'}
    
    def restart_plugin(self, plugin_id: str) -> Dict[str, Any]:
        """Restart a plugin by stopping and starting it"""
        stop_result = self.stop_plugin(plugin_id)
        if not stop_result.get('success') and 'already stopped' not in stop_result.get('message', ''):
            return stop_result
        
        time.sleep(1)
        return self.start_plugin(plugin_id)
    
    def enable_plugin(self, plugin_id: str) -> Dict[str, Any]:
        """Enable a plugin"""
        if not self.registry.is_registered(plugin_id):
            return {'success': False, 'error': f'Plugin not found: {plugin_id}'}
        
        success = self.registry.update_plugin_config(plugin_id, {'enabled': True})
        if success:
            self._plugin_status[plugin_id] = PluginStatus.STOPPED
            logger.info(f"Plugin enabled: {plugin_id}")
            return {'success': True, 'message': 'Plugin enabled'}
        return {'success': False, 'error': 'Failed to enable plugin'}
    
    def disable_plugin(self, plugin_id: str) -> Dict[str, Any]:
        """Disable a plugin (stops it first if running)"""
        if not self.registry.is_registered(plugin_id):
            return {'success': False, 'error': f'Plugin not found: {plugin_id}'}
        
        # Stop if running
        if self._plugin_status.get(plugin_id) == PluginStatus.RUNNING:
            self.stop_plugin(plugin_id)
        
        success = self.registry.update_plugin_config(plugin_id, {'enabled': False})
        if success:
            self._plugin_status[plugin_id] = PluginStatus.DISABLED
            logger.info(f"Plugin disabled: {plugin_id}")
            return {'success': True, 'message': 'Plugin disabled'}
        return {'success': False, 'error': 'Failed to disable plugin'}
    
    def health_check(self, plugin_id: str) -> Dict[str, Any]:
        """
        Get health status of a plugin.
        
        Args:
            plugin_id: Plugin ID
            
        Returns:
            Health status dictionary
        """
        metadata = self.registry.get_plugin(plugin_id)
        if not metadata:
            return {'status': 'unknown', 'error': 'Plugin not found'}
        
        if not metadata.config.enabled:
            return {'status': 'disabled', 'enabled': False}
        
        if metadata.type == 'service':
            health_endpoint = metadata.endpoints.get('health', '/health')
            service_url = metadata.config.service_url
            
            if not service_url and metadata.port:
                service_url = f"http://localhost:{metadata.port}"
            
            if not service_url:
                return {'status': 'error', 'error': 'No service URL configured'}
            
            try:
                url = f"{service_url.rstrip('/')}{health_endpoint}"
                response = requests.get(url, timeout=5)
                
                if response.status_code == 200:
                    data = response.json()
                    data['plugin_id'] = plugin_id
                    return data
                else:
                    return {
                        'status': 'unhealthy',
                        'http_status': response.status_code,
                        'plugin_id': plugin_id
                    }
            except requests.exceptions.ConnectionError:
                return {'status': 'stopped', 'error': 'Service not running'}
            except Exception as e:
                return {'status': 'error', 'error': str(e)}
        
        return {'status': 'unknown', 'type': metadata.type}
    
    def get_plugin_info(self, plugin_id: str) -> Dict[str, Any]:
        """Get comprehensive plugin information"""
        metadata = self.registry.get_plugin(plugin_id)
        if not metadata:
            return {'error': f'Plugin not found: {plugin_id}'}
        
        plugin_dir = self.registry.get_plugin_dir(plugin_id)
        status = self._plugin_status.get(plugin_id, PluginStatus.UNKNOWN)
        
        info = metadata.to_dict()
        info['status'] = status.value
        info['running'] = status == PluginStatus.RUNNING
        info['plugin_dir'] = str(plugin_dir) if plugin_dir else None
        
        # Add health info if running
        if status == PluginStatus.RUNNING:
            health = self.health_check(plugin_id)
            info['health'] = health
        
        return info
    
    def list_plugins(self) -> List[Dict[str, Any]]:
        """List all plugins with their current status"""
        self._refresh_status()
        
        result = []
        for plugin_info in self.registry.list_plugins():
            plugin_id = plugin_info['id']
            status = self._plugin_status.get(plugin_id, PluginStatus.UNKNOWN)
            plugin_info['status'] = status.value
            plugin_info['running'] = status == PluginStatus.RUNNING
            result.append(plugin_info)
        
        return result


# Global manager instance
_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """Get the global plugin manager instance"""
    global _manager
    if _manager is None:
        _manager = PluginManager()
    return _manager
