"""
Service Plugin Implementation
Handles plugins that run as standalone services.
"""

import logging
import subprocess
import time
import requests
from pathlib import Path
from typing import Dict, Any, Optional

from .plugin_base import PluginBase, PluginStatus

logger = logging.getLogger(__name__)


class ServicePlugin(PluginBase):
    """
    Plugin implementation for standalone service plugins.

    Service plugins run as separate processes (e.g., Flask apps)
    and communicate via HTTP.
    """

    def __init__(self, plugin_dir: Path):
        super().__init__(plugin_dir)
        self._pid: Optional[int] = None

    @property
    def service_url(self) -> str:
        """Get the service URL"""
        if self.metadata and self.metadata.config.service_url:
            return self.metadata.config.service_url
        if self.metadata and self.metadata.port:
            return f"http://localhost:{self.metadata.port}"
        return "http://localhost:5002"

    @property
    def health_endpoint(self) -> str:
        """Get the health check endpoint"""
        if self.metadata and self.metadata.endpoints:
            return self.metadata.endpoints.get("health", "/health")
        return "/health"

    def start(self) -> bool:
        """Start the service plugin"""
        if self.status == PluginStatus.RUNNING:
            logger.info(f"Plugin {self.id} is already running")
            return True

        if not self.is_enabled:
            logger.warning(f"Cannot start disabled plugin: {self.id}")
            return False

        self.status = PluginStatus.STARTING

        # Look for start script
        start_script = self.plugin_dir / "scripts" / "start.sh"
        if not start_script.exists():
            logger.error(f"Start script not found: {start_script}")
            self.status = PluginStatus.ERROR
            return False

        try:
            result = subprocess.run(
                ["bash", str(start_script)],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.plugin_dir),
            )

            if result.returncode != 0:
                logger.error(f"Start script failed: {result.stderr}")
                self.status = PluginStatus.ERROR
                return False

            # Wait and check if service is healthy
            time.sleep(2)
            health = self.health_check()

            if health.get("status") in ("healthy", "degraded"):
                self.status = PluginStatus.RUNNING
                logger.info(f"Plugin started: {self.id}")
                return True
            else:
                self.status = PluginStatus.ERROR
                logger.error(f"Plugin started but unhealthy: {health}")
                return False

        except subprocess.TimeoutExpired:
            self.status = PluginStatus.ERROR
            logger.error(f"Start script timed out for plugin: {self.id}")
            return False
        except Exception as e:
            self.status = PluginStatus.ERROR
            logger.error(f"Failed to start plugin {self.id}: {e}")
            return False

    def stop(self) -> bool:
        """Stop the service plugin"""
        if self.status == PluginStatus.STOPPED:
            logger.info(f"Plugin {self.id} is already stopped")
            return True

        self.status = PluginStatus.STOPPING

        # Look for stop script
        stop_script = self.plugin_dir / "scripts" / "stop.sh"
        if not stop_script.exists():
            logger.error(f"Stop script not found: {stop_script}")
            self.status = PluginStatus.ERROR
            return False

        try:
            result = subprocess.run(
                ["bash", str(stop_script)],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.plugin_dir),
            )

            # Check if service is actually stopped
            time.sleep(1)
            health = self.health_check()

            if health.get("status") == "stopped" or "error" in health:
                self.status = PluginStatus.STOPPED
                logger.info(f"Plugin stopped: {self.id}")
                return True
            else:
                self.status = PluginStatus.RUNNING
                logger.warning(f"Plugin stop script ran but service still running")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"Stop script timed out for plugin: {self.id}")
            return False
        except Exception as e:
            logger.error(f"Failed to stop plugin {self.id}: {e}")
            return False

    def health_check(self) -> Dict[str, Any]:
        """Check health of the service"""
        url = f"{self.service_url.rstrip('/')}{self.health_endpoint}"

        try:
            response = requests.get(url, timeout=5)

            if response.status_code == 200:
                data = response.json()
                self.status = PluginStatus.RUNNING
                return data
            elif response.status_code == 503:
                data = response.json()
                self.status = PluginStatus.RUNNING  # Service is up but degraded
                return data
            else:
                return {"status": "unhealthy", "http_status": response.status_code}

        except requests.exceptions.ConnectionError:
            self.status = PluginStatus.STOPPED
            return {"status": "stopped", "error": "Connection refused"}
        except requests.exceptions.Timeout:
            return {"status": "timeout", "error": "Health check timed out"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
