# backend/utils/blueprint_discovery.py
# Dynamic Blueprint Discovery and Registration System
# Eliminates manual blueprint registration and automates discovery

import os
import importlib
import logging
from typing import List, Dict, Tuple, Optional, Any
from pathlib import Path
from flask import Flask, Blueprint

logger = logging.getLogger(__name__)

class BlueprintDiscovery:
    """
    Automated blueprint discovery and registration system.
    Scans specified directories for Flask blueprints and registers them automatically.
    """
    
    def __init__(self, app: Optional[Flask] = None):
        self.app = app
        self.discovered_blueprints: List[Dict[str, Any]] = []
        self.registration_errors: List[Dict[str, str]] = []
        self.excluded_modules = {
            '__init__', '__pycache__', 'tests', 
            'conftest', 'setup', 'config'
        }
        
    def should_exclude_module(self, module_name: str) -> bool:
        """Check if a module should be excluded from blueprint discovery"""
        if not module_name or module_name.startswith('_'):
            return True
            
        for excluded in self.excluded_modules:
            if excluded in module_name.lower():
                return True
                
        return False
    
    def discover_blueprints_in_directory(
        self, 
        directory_path: str, 
        package_prefix: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Discover all blueprints in a given directory.
        
        Args:
            directory_path: Path to directory to scan
            package_prefix: Python package prefix (e.g., "backend.api")
            
        Returns:
            List of discovered blueprint information
        """
        blueprints = []
        directory = Path(directory_path)
        
        if not directory.exists() or not directory.is_dir():
            logger.warning(f"Directory not found: {directory_path}")
            return blueprints
            
        logger.info(f"Scanning directory for blueprints: {directory_path}")

        # Scan all Python files in the directory
        for py_file in directory.glob("*.py"):
            module_name = py_file.stem

            if self.should_exclude_module(module_name):
                logger.debug(f"Excluding module: {module_name}")
                continue

            try:
                # Construct full module path
                if package_prefix:
                    full_module_name = f"{package_prefix}.{module_name}"
                else:
                    full_module_name = module_name

                # Import the module
                logger.debug(f"Importing module: {full_module_name}")
                module = importlib.import_module(full_module_name)

                # Find blueprint objects in the module
                module_blueprints = self.find_blueprints_in_module(module, full_module_name)
                blueprints.extend(module_blueprints)

            except ImportError as e:
                error_msg = f"Failed to import {full_module_name}: {e}"
                logger.warning(error_msg)
                self.registration_errors.append({
                    "module": full_module_name,
                    "error": error_msg,
                    "type": "import_error"
                })
            except Exception as e:
                error_msg = f"Error processing {full_module_name}: {e}"
                logger.error(error_msg)
                self.registration_errors.append({
                    "module": full_module_name,
                    "error": error_msg,
                    "type": "processing_error"
                })

        # Also scan subdirectories that are Python packages (have __init__.py)
        for subdir in directory.iterdir():
            if not subdir.is_dir():
                continue
            if subdir.name.startswith('_') or subdir.name in self.excluded_modules:
                continue
            # Check if it's a Python package
            init_file = subdir / "__init__.py"
            if not init_file.exists():
                continue

            # Recursively scan the subpackage
            if package_prefix:
                sub_package_prefix = f"{package_prefix}.{subdir.name}"
            else:
                sub_package_prefix = subdir.name

            logger.info(f"Scanning subpackage: {sub_package_prefix}")
            sub_blueprints = self.discover_blueprints_in_directory(str(subdir), sub_package_prefix)
            blueprints.extend(sub_blueprints)

        return blueprints
    
    def find_blueprints_in_module(self, module: Any, module_name: str) -> List[Dict[str, Any]]:
        """
        Find all Blueprint objects in a given module.
        
        Args:
            module: The imported module
            module_name: Name of the module
            
        Returns:
            List of blueprint information dictionaries
        """
        blueprints = []
        
        # Iterate through all attributes in the module
        for attr_name in dir(module):
            if attr_name.startswith('_'):
                continue
                
            try:
                attr_value = getattr(module, attr_name)
                
                # Check if the attribute is a Blueprint instance
                if isinstance(attr_value, Blueprint):
                    blueprint_info = {
                        "blueprint": attr_value,
                        "name": attr_value.name,
                        "attribute_name": attr_name,
                        "module_name": module_name,
                        "url_prefix": getattr(attr_value, "url_prefix", None),
                        "import_name": attr_value.import_name
                    }
                    
                    blueprints.append(blueprint_info)
                    logger.info(f"✓ Discovered blueprint: {attr_name} ({attr_value.name}) in {module_name} with prefix {blueprint_info['url_prefix']}")
                    
            except Exception as e:
                logger.debug(f"Error inspecting attribute {attr_name} in {module_name}: {e}")
                continue
                
        return blueprints
    
    def register_blueprints(self, app: Flask, blueprints: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Register discovered blueprints with the Flask app.
        
        Args:
            app: Flask application instance
            blueprints: List of blueprint information dictionaries
            
        Returns:
            Registration summary with counts and errors
        """
        registered_count = 0
        skipped_count = 0
        error_count = 0
        
        for blueprint_info in blueprints:
            blueprint = blueprint_info["blueprint"]
            blueprint_name = blueprint_info["name"]
            
            try:
                # Check if blueprint is already registered
                if blueprint_name in app.blueprints:
                    logger.debug(f"Skipping already registered blueprint: {blueprint_name}")
                    skipped_count += 1
                    continue
                
                # Register the blueprint
                app.register_blueprint(blueprint)
                logger.info(f"Registered blueprint: {blueprint_name} from {blueprint_info['module_name']}")
                registered_count += 1
                
            except Exception as e:
                error_msg = f"Failed to register blueprint {blueprint_name}: {e}"
                logger.error(error_msg)
                self.registration_errors.append({
                    "blueprint": blueprint_name,
                    "module": blueprint_info["module_name"],
                    "error": error_msg,
                    "type": "registration_error"
                })
                error_count += 1
        
        return {
            "registered": registered_count,
            "skipped": skipped_count,
            "errors": error_count,
            "total_discovered": len(blueprints)
        }
    
    def auto_discover_and_register(
        self, 
        app: Flask, 
        directories: List[Tuple[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Automatically discover and register blueprints from specified directories.
        
        Args:
            app: Flask application instance
            directories: List of (directory_path, package_prefix) tuples
            
        Returns:
            Complete registration summary
        """
        if directories is None:
            # Default directories to scan (absolute paths from backend directory)
            import os
            current_dir = os.getcwd()
            backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

            # Try to find the api and routes directories
            api_dir = os.path.join(backend_dir, "api")
            routes_dir = os.path.join(backend_dir, "routes")

            # Debug logging
            logger.debug(f"Current working directory: {current_dir}")
            logger.debug(f"Backend directory: {backend_dir}")
            logger.debug(f"API directory: {api_dir} (exists: {os.path.exists(api_dir)})")
            logger.debug(f"Routes directory: {routes_dir} (exists: {os.path.exists(routes_dir)})")

            directories = [
                (api_dir, "backend.api"),
                (routes_dir, "backend.routes")
            ]
        
        all_blueprints = []
        discovery_summary = {}
        
        # Discover blueprints in each directory
        for directory_path, package_prefix in directories:
            logger.info(f"Discovering blueprints in: {directory_path}")
            
            dir_blueprints = self.discover_blueprints_in_directory(directory_path, package_prefix)
            all_blueprints.extend(dir_blueprints)
            
            discovery_summary[directory_path] = {
                "discovered_count": len(dir_blueprints),
                "blueprints": [bp["name"] for bp in dir_blueprints]
            }
        
        # Store discovered blueprints
        self.discovered_blueprints = all_blueprints
        
        # Register all discovered blueprints
        registration_summary = self.register_blueprints(app, all_blueprints)
        
        # Combine discovery and registration summaries
        complete_summary = {
            "discovery": discovery_summary,
            "registration": registration_summary,
            "errors": self.registration_errors,
            "blueprint_details": [
                {
                    "name": bp["name"],
                    "module": bp["module_name"],
                    "url_prefix": bp["url_prefix"]
                }
                for bp in all_blueprints
            ]
        }
        
        # Log summary
        self.log_registration_summary(complete_summary)
        
        return complete_summary
    
    def log_registration_summary(self, summary: Dict[str, Any]) -> None:
        """Log a comprehensive summary of blueprint registration"""
        registration = summary["registration"]
        
        logger.info("=" * 60)
        logger.info("BLUEPRINT REGISTRATION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total Discovered: {registration['total_discovered']}")
        logger.info(f"Successfully Registered: {registration['registered']}")
        logger.info(f"Already Registered (Skipped): {registration['skipped']}")
        logger.info(f"Registration Errors: {registration['errors']}")
        
        if summary["blueprint_details"]:
            logger.info("\nRegistered Blueprints:")
            for blueprint in summary["blueprint_details"]:
                prefix = blueprint["url_prefix"] or "No prefix"
                logger.info(f"  - {blueprint['name']} ({prefix}) from {blueprint['module']}")
        
        if summary["errors"]:
            logger.warning(f"\nErrors encountered ({len(summary['errors'])}):")
            for error in summary["errors"]:
                logger.warning(f"  - {error['type']}: {error['error']}")
        
        logger.info("=" * 60)
    
    def get_registration_report(self) -> Dict[str, Any]:
        """Get a detailed registration report for debugging"""
        return {
            "discovered_blueprints": [
                {
                    "name": bp["name"],
                    "module": bp["module_name"],
                    "attribute": bp["attribute_name"],
                    "url_prefix": bp["url_prefix"],
                    "import_name": bp["import_name"]
                }
                for bp in self.discovered_blueprints
            ],
            "errors": self.registration_errors,
            "summary": {
                "total_discovered": len(self.discovered_blueprints),
                "total_errors": len(self.registration_errors)
            }
        }

def auto_register_blueprints(app: Flask, **kwargs) -> Dict[str, Any]:
    """
    Convenience function for automatic blueprint registration.
    
    Args:
        app: Flask application instance
        **kwargs: Additional arguments for BlueprintDiscovery
        
    Returns:
        Registration summary
    """
    discovery = BlueprintDiscovery(app)
    return discovery.auto_discover_and_register(app, **kwargs)

# Legacy compatibility - maintain existing patterns
def register_api_blueprints(app: Flask) -> Dict[str, Any]:
    """
    Register blueprints from the backend.api package.
    Maintains compatibility with existing registration patterns.
    """
    discovery = BlueprintDiscovery(app)
    return discovery.auto_discover_and_register(
        app, 
        directories=[("backend/api", "backend.api")]
    )

def register_route_blueprints(app: Flask) -> Dict[str, Any]:
    """
    Register blueprints from the backend.routes package.
    """
    discovery = BlueprintDiscovery(app)
    return discovery.auto_discover_and_register(
        app, 
        directories=[("backend/routes", "backend.routes")]
    ) 