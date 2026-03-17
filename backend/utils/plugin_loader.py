import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from flask import Flask

logger = logging.getLogger(__name__)


class PluginLoader:

    def __init__(self, app: Flask, plugins_dir: Optional[str] = None):
        self.app = app
        self.plugins_dir = plugins_dir or self._get_default_plugins_dir()
        self.loaded_plugins: Dict[str, object] = {}
        self.plugin_manifests: Dict[str, dict] = {}

    def _get_default_plugins_dir(self) -> str:
        llamax_root = self.app.config.get("GUAARDVARK_ROOT")
        if llamax_root:
            return os.path.join(llamax_root, "backend", "plugins")
        else:
            return os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugins")

    def discover_plugins(self) -> List[str]:
        if not os.path.exists(self.plugins_dir):
            logger.info("Plugins directory does not exist: %s", self.plugins_dir)
            logger.info("No plugins will be loaded (this is fine)")
            return []

        plugins = []
        for entry in os.listdir(self.plugins_dir):
            if entry.startswith("__") or entry.startswith("."):
                continue

            plugin_path = os.path.join(self.plugins_dir, entry)
            manifest_path = os.path.join(plugin_path, "manifest.json")

            if os.path.isdir(plugin_path) and os.path.exists(manifest_path):
                try:
                    with open(manifest_path, "r") as f:
                        manifest = json.load(f)
                        plugin_id = manifest.get("id", entry)
                        plugins.append(plugin_id)
                        self.plugin_manifests[plugin_id] = manifest
                        logger.debug(
                            "Discovered plugin: %s (v%s)",
                            manifest.get("name", plugin_id),
                            manifest.get("version", "unknown"),
                        )
                except Exception as e:
                    logger.error("Failed to read manifest for %s: %s", entry, e)

        return plugins

    def load_plugin(self, plugin_id: str):
        plugin_path = os.path.join(self.plugins_dir, plugin_id)

        if not os.path.exists(plugin_path):
            raise ValueError(f"Plugin not found: {plugin_id}")

        try:
            import importlib.util
            import sys

            init_file = os.path.join(plugin_path, "__init__.py")

            if not os.path.exists(init_file):
                raise ImportError(f"Plugin {plugin_id} has no __init__.py")

            spec = importlib.util.spec_from_file_location(
                f"backend.plugins.{plugin_id}", init_file
            )

            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load plugin spec for {plugin_id}")

            module = importlib.util.module_from_spec(spec)

            sys.modules[f"backend.plugins.{plugin_id}"] = module

            spec.loader.exec_module(module)

            if hasattr(module, "init_plugin"):
                plugin_instance = module.init_plugin(self.app)
                self.loaded_plugins[plugin_id] = plugin_instance

                manifest = self.plugin_manifests.get(plugin_id, {})
                logger.info(
                    "✅ Loaded plugin: %s (v%s)",
                    manifest.get("name", plugin_id),
                    manifest.get("version", "unknown"),
                )
            else:
                logger.warning("Plugin %s has no init_plugin() function", plugin_id)

        except Exception as e:
            logger.error("❌ Failed to load plugin %s: %s", plugin_id, e, exc_info=True)
            raise

    def load_all_plugins(self):
        plugins = self.discover_plugins()

        if not plugins:
            logger.info("No plugins found in %s", self.plugins_dir)
            return

        logger.info("Discovered %d plugin(s): %s", len(plugins), ", ".join(plugins))

        for plugin_id in plugins:
            try:
                self.load_plugin(plugin_id)
            except Exception as e:
                logger.error("Skipping plugin %s due to error: %s", plugin_id, e)

    def get_plugin(self, plugin_id: str):
        return self.loaded_plugins.get(plugin_id)

    def get_all_plugins(self) -> Dict[str, object]:
        return self.loaded_plugins.copy()

    def get_plugin_info(self, plugin_id: str) -> Optional[dict]:
        return self.plugin_manifests.get(plugin_id)

    def shutdown_all_plugins(self):
        for plugin_id, plugin_instance in self.loaded_plugins.items():
            try:
                if hasattr(plugin_instance, "shutdown"):
                    plugin_instance.shutdown()
                    logger.debug("Shutdown plugin: %s", plugin_id)
            except Exception as e:
                logger.error("Error shutting down plugin %s: %s", plugin_id, e)


def init_plugins(app: Flask) -> PluginLoader:
    logger.info("🔌 Initializing plugin system")

    loader = PluginLoader(app)
    loader.load_all_plugins()

    app.plugin_loader = loader

    return loader
