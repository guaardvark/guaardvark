#!/usr/bin/env python3
"""
Integration tests for the Plugin System.

Tests the plugin infrastructure including:
- Plugin discovery and registration
- Plugin lifecycle management (start/stop/enable/disable)
- Plugin API endpoints
- Plugin configuration persistence

These tests can run without the GPU embedding service running.
"""

import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Skip if Flask not available
try:
    from flask import Flask
    from backend.api.plugins_api import plugins_bp
    from backend.plugins import PluginManager, PluginRegistry, PluginStatus
    from backend.plugins.plugin_base import PluginMetadata, PluginConfig
except ImportError as e:
    pytest.skip(f"Plugin modules not available: {e}", allow_module_level=True)


@pytest.fixture
def plugins_dir(tmp_path):
    """Create a temporary plugins directory with test plugin."""
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    
    # Create a test plugin
    test_plugin_dir = plugins_root / "test-plugin"
    test_plugin_dir.mkdir()
    
    # Create plugin.json
    plugin_json = {
        "id": "test-plugin",
        "name": "Test Plugin",
        "version": "1.0.0",
        "description": "A test plugin for unit testing",
        "author": "Test Author",
        "type": "service",
        "category": "testing",
        "port": 9999,
        "dependencies": [],
        "config": {
            "enabled": False,
            "service_url": "http://localhost:9999",
            "timeout": 10,
            "fallback_enabled": True
        },
        "requirements": {
            "gpu": False,
            "cuda": False
        },
        "endpoints": {
            "health": "/health"
        }
    }
    
    with open(test_plugin_dir / "plugin.json", "w") as f:
        json.dump(plugin_json, f, indent=2)
    
    # Create scripts directory with mock scripts
    scripts_dir = test_plugin_dir / "scripts"
    scripts_dir.mkdir()
    
    # Create mock start script
    start_script = scripts_dir / "start.sh"
    start_script.write_text("#!/bin/bash\necho 'Started'\nexit 0\n")
    start_script.chmod(0o755)
    
    # Create mock stop script
    stop_script = scripts_dir / "stop.sh"
    stop_script.write_text("#!/bin/bash\necho 'Stopped'\nexit 0\n")
    stop_script.chmod(0o755)
    
    return plugins_root


@pytest.fixture
def app(plugins_dir):
    """Create Flask app with plugins API."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    
    if plugins_bp.name not in app.blueprints:
        app.register_blueprint(plugins_bp)
    
    return app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


class TestPluginRegistry:
    """Tests for plugin discovery and registration."""
    
    def test_discover_plugins(self, plugins_dir):
        """Test plugin discovery from plugins directory."""
        registry = PluginRegistry(plugins_dir=plugins_dir)
        
        assert registry.is_registered("test-plugin")
        assert len(registry.list_plugins()) == 1
    
    def test_get_plugin_metadata(self, plugins_dir):
        """Test getting plugin metadata by ID."""
        registry = PluginRegistry(plugins_dir=plugins_dir)
        
        metadata = registry.get_plugin("test-plugin")
        
        assert metadata is not None
        assert metadata.id == "test-plugin"
        assert metadata.name == "Test Plugin"
        assert metadata.version == "1.0.0"
        assert metadata.type == "service"
        assert metadata.port == 9999
    
    def test_get_plugin_config(self, plugins_dir):
        """Test getting plugin configuration."""
        registry = PluginRegistry(plugins_dir=plugins_dir)
        
        metadata = registry.get_plugin("test-plugin")
        
        assert metadata.config.enabled == False
        assert metadata.config.service_url == "http://localhost:9999"
        assert metadata.config.timeout == 10
    
    def test_update_plugin_config(self, plugins_dir):
        """Test updating plugin configuration."""
        registry = PluginRegistry(plugins_dir=plugins_dir)
        
        # Update config
        success = registry.update_plugin_config("test-plugin", {"enabled": True, "timeout": 60})
        
        assert success
        
        # Verify update persisted
        metadata = registry.get_plugin("test-plugin")
        assert metadata.config.enabled == True
        assert metadata.config.timeout == 60
        
        # Verify plugin.json was updated
        plugin_json_path = plugins_dir / "test-plugin" / "plugin.json"
        with open(plugin_json_path) as f:
            saved_config = json.load(f)
        
        assert saved_config["config"]["enabled"] == True
        assert saved_config["config"]["timeout"] == 60
    
    def test_list_plugins_by_type(self, plugins_dir):
        """Test filtering plugins by type."""
        registry = PluginRegistry(plugins_dir=plugins_dir)
        
        service_plugins = registry.get_plugins_by_type("service")
        
        assert len(service_plugins) == 1
        assert service_plugins[0].id == "test-plugin"
    
    def test_refresh_registry(self, plugins_dir):
        """Test refreshing the plugin registry."""
        registry = PluginRegistry(plugins_dir=plugins_dir)
        
        # Add another plugin
        new_plugin_dir = plugins_dir / "new-plugin"
        new_plugin_dir.mkdir()
        
        new_plugin_json = {
            "id": "new-plugin",
            "name": "New Plugin",
            "version": "1.0.0",
            "type": "extension",
            "config": {"enabled": False}
        }
        
        with open(new_plugin_dir / "plugin.json", "w") as f:
            json.dump(new_plugin_json, f)
        
        # Refresh and verify new plugin is discovered
        discovered = registry.refresh()
        
        assert "new-plugin" in discovered
        assert registry.is_registered("new-plugin")


class TestPluginManager:
    """Tests for plugin lifecycle management."""
    
    def test_get_plugin_status_disabled(self, plugins_dir):
        """Test status of disabled plugin."""
        registry = PluginRegistry(plugins_dir=plugins_dir)
        manager = PluginManager(registry=registry)
        
        status = manager.get_status("test-plugin")
        
        assert status == PluginStatus.DISABLED
    
    def test_enable_plugin(self, plugins_dir):
        """Test enabling a plugin."""
        registry = PluginRegistry(plugins_dir=plugins_dir)
        manager = PluginManager(registry=registry)
        
        result = manager.enable_plugin("test-plugin")
        
        assert result["success"] == True
        assert manager.get_status("test-plugin") == PluginStatus.STOPPED
    
    def test_disable_plugin(self, plugins_dir):
        """Test disabling a plugin."""
        registry = PluginRegistry(plugins_dir=plugins_dir)
        manager = PluginManager(registry=registry)
        
        # First enable the plugin
        manager.enable_plugin("test-plugin")
        
        # Then disable it
        result = manager.disable_plugin("test-plugin")
        
        assert result["success"] == True
        assert manager.get_status("test-plugin") == PluginStatus.DISABLED
    
    def test_get_plugin_info(self, plugins_dir):
        """Test getting comprehensive plugin information."""
        registry = PluginRegistry(plugins_dir=plugins_dir)
        manager = PluginManager(registry=registry)
        
        info = manager.get_plugin_info("test-plugin")
        
        assert info["id"] == "test-plugin"
        assert info["name"] == "Test Plugin"
        assert info["status"] == "disabled"
        assert info["running"] == False
        assert "config" in info
    
    def test_list_all_plugins(self, plugins_dir):
        """Test listing all plugins with status."""
        registry = PluginRegistry(plugins_dir=plugins_dir)
        manager = PluginManager(registry=registry)
        
        plugins = manager.list_plugins()
        
        assert len(plugins) == 1
        assert plugins[0]["id"] == "test-plugin"
        assert "status" in plugins[0]
        assert "running" in plugins[0]
    
    @patch('backend.plugins.plugin_manager.requests.get')
    def test_health_check_service_unavailable(self, mock_get, plugins_dir):
        """Test health check when service is not running."""
        mock_get.side_effect = Exception("Connection refused")
        
        registry = PluginRegistry(plugins_dir=plugins_dir)
        manager = PluginManager(registry=registry)
        
        # Enable plugin first
        manager.enable_plugin("test-plugin")
        
        health = manager.health_check("test-plugin")
        
        assert health["status"] in ("stopped", "error")
    
    def test_start_plugin_not_enabled(self, plugins_dir):
        """Test starting a disabled plugin fails."""
        registry = PluginRegistry(plugins_dir=plugins_dir)
        manager = PluginManager(registry=registry)
        
        result = manager.start_plugin("test-plugin")
        
        assert result["success"] == False
        assert "disabled" in result["error"].lower() or "enable" in result["error"].lower()


class TestPluginsAPI:
    """Tests for plugin API endpoints."""
    
    def test_list_plugins_endpoint(self, app, client, plugins_dir):
        """Test GET /api/plugins endpoint."""
        with patch('backend.plugins.plugin_registry.get_plugin_registry') as mock_registry:
            mock_reg = PluginRegistry(plugins_dir=plugins_dir)
            mock_registry.return_value = mock_reg
            
            with patch('backend.plugins.plugin_manager.get_plugin_manager') as mock_manager:
                mock_mgr = PluginManager(registry=mock_reg)
                mock_manager.return_value = mock_mgr
                
                response = client.get("/api/plugins")
                
                assert response.status_code == 200
                data = response.get_json()
                assert data["success"] == True
                assert "plugins" in data["data"]
    
    def test_get_plugin_endpoint(self, app, client, plugins_dir):
        """Test GET /api/plugins/<id> endpoint."""
        with patch('backend.plugins.plugin_registry.get_plugin_registry') as mock_registry:
            mock_reg = PluginRegistry(plugins_dir=plugins_dir)
            mock_registry.return_value = mock_reg
            
            with patch('backend.plugins.plugin_manager.get_plugin_manager') as mock_manager:
                mock_mgr = PluginManager(registry=mock_reg)
                mock_manager.return_value = mock_mgr
                
                response = client.get("/api/plugins/test-plugin")
                
                assert response.status_code == 200
                data = response.get_json()
                assert data["success"] == True
                assert data["data"]["id"] == "test-plugin"
    
    def test_get_plugin_not_found(self, app, client, plugins_dir):
        """Test GET /api/plugins/<id> with non-existent plugin."""
        with patch('backend.plugins.plugin_registry.get_plugin_registry') as mock_registry:
            mock_reg = PluginRegistry(plugins_dir=plugins_dir)
            mock_registry.return_value = mock_reg
            
            with patch('backend.plugins.plugin_manager.get_plugin_manager') as mock_manager:
                mock_mgr = PluginManager(registry=mock_reg)
                mock_manager.return_value = mock_mgr
                
                response = client.get("/api/plugins/nonexistent")
                
                assert response.status_code == 404
    
    def test_enable_plugin_endpoint(self, app, client, plugins_dir):
        """Test POST /api/plugins/<id>/enable endpoint."""
        with patch('backend.plugins.plugin_registry.get_plugin_registry') as mock_registry:
            mock_reg = PluginRegistry(plugins_dir=plugins_dir)
            mock_registry.return_value = mock_reg
            
            with patch('backend.plugins.plugin_manager.get_plugin_manager') as mock_manager:
                mock_mgr = PluginManager(registry=mock_reg)
                mock_manager.return_value = mock_mgr
                
                response = client.post("/api/plugins/test-plugin/enable")
                
                assert response.status_code == 200
                data = response.get_json()
                assert data["success"] == True
    
    def test_disable_plugin_endpoint(self, app, client, plugins_dir):
        """Test POST /api/plugins/<id>/disable endpoint."""
        with patch('backend.plugins.plugin_registry.get_plugin_registry') as mock_registry:
            mock_reg = PluginRegistry(plugins_dir=plugins_dir)
            mock_registry.return_value = mock_reg
            
            with patch('backend.plugins.plugin_manager.get_plugin_manager') as mock_manager:
                mock_mgr = PluginManager(registry=mock_reg)
                mock_manager.return_value = mock_mgr
                
                # First enable
                client.post("/api/plugins/test-plugin/enable")
                
                # Then disable
                response = client.post("/api/plugins/test-plugin/disable")
                
                assert response.status_code == 200
                data = response.get_json()
                assert data["success"] == True
    
    def test_update_config_endpoint(self, app, client, plugins_dir):
        """Test PUT /api/plugins/<id>/config endpoint."""
        with patch('backend.plugins.plugin_registry.get_plugin_registry') as mock_registry:
            mock_reg = PluginRegistry(plugins_dir=plugins_dir)
            mock_registry.return_value = mock_reg
            
            with patch('backend.plugins.plugin_manager.get_plugin_manager') as mock_manager:
                mock_mgr = PluginManager(registry=mock_reg)
                mock_manager.return_value = mock_mgr
                
                response = client.put(
                    "/api/plugins/test-plugin/config",
                    json={"timeout": 120}
                )
                
                assert response.status_code == 200
                data = response.get_json()
                assert data["success"] == True
    
    def test_refresh_plugins_endpoint(self, app, client, plugins_dir):
        """Test POST /api/plugins/refresh endpoint."""
        with patch('backend.plugins.plugin_registry.get_plugin_registry') as mock_registry:
            mock_reg = PluginRegistry(plugins_dir=plugins_dir)
            mock_registry.return_value = mock_reg
            
            with patch('backend.plugins.plugin_manager.get_plugin_manager') as mock_manager:
                mock_mgr = PluginManager(registry=mock_reg)
                mock_manager.return_value = mock_mgr
                
                response = client.post("/api/plugins/refresh")
                
                assert response.status_code == 200
                data = response.get_json()
                assert data["success"] == True
                assert "discovered" in data["data"]


class TestPluginMetadata:
    """Tests for plugin metadata handling."""
    
    def test_load_metadata_from_file(self, plugins_dir):
        """Test loading plugin metadata from plugin.json."""
        plugin_json_path = plugins_dir / "test-plugin" / "plugin.json"
        
        metadata = PluginMetadata.from_json_file(plugin_json_path)
        
        assert metadata.id == "test-plugin"
        assert metadata.name == "Test Plugin"
        assert metadata.version == "1.0.0"
        assert metadata.type == "service"
        assert metadata.port == 9999
    
    def test_metadata_to_dict(self, plugins_dir):
        """Test converting metadata to dictionary."""
        plugin_json_path = plugins_dir / "test-plugin" / "plugin.json"
        metadata = PluginMetadata.from_json_file(plugin_json_path)
        
        data = metadata.to_dict()
        
        assert isinstance(data, dict)
        assert data["id"] == "test-plugin"
        assert "config" in data
        assert isinstance(data["config"], dict)
    
    def test_save_metadata(self, plugins_dir):
        """Test saving plugin metadata back to file."""
        plugin_json_path = plugins_dir / "test-plugin" / "plugin.json"
        metadata = PluginMetadata.from_json_file(plugin_json_path)
        
        # Modify and save
        metadata.config.enabled = True
        metadata.save(plugin_json_path)
        
        # Reload and verify
        reloaded = PluginMetadata.from_json_file(plugin_json_path)
        assert reloaded.config.enabled == True


if __name__ == "__main__":
    pytest.main([__file__, "-vv", "--tb=short"])
