#!/usr/bin/env python3
"""
Test Mapper - Matches tests to components and identifies coverage gaps
"""
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Set
from collections import defaultdict

class TestMapper:
    """Maps tests to components and analyzes test coverage"""
    
    def __init__(self, scan_file: str, root_dir: str):
        """Initialize with component scan results"""
        with open(scan_file, 'r', encoding='utf-8') as f:
            self.scan_data = json.load(f)
        
        self.components = self.scan_data.get('components', [])
        self.root_dir = Path(root_dir)
        self.test_files: List[Dict] = []
        self.test_to_component_map: Dict[str, List[str]] = defaultdict(list)
        self.component_to_test_map: Dict[str, List[str]] = defaultdict(list)
        
    def extract_test_info(self, test_file: Path) -> Dict:
        """Extract information from a test file"""
        try:
            with open(test_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract test functions
            test_functions = re.findall(r'def\s+(test_\w+)', content)
            
            # Extract test classes
            test_classes = re.findall(r'class\s+(Test\w+)', content)
            
            # Try to identify what's being tested
            tested_modules = set()
            
            # Look for imports
            import_pattern = r'from\s+backend\.(\w+(?:\.\w+)*)\s+import|import\s+backend\.(\w+(?:\.\w+)*)'
            imports = re.findall(import_pattern, content)
            for imp in imports:
                module = imp[0] or imp[1]
                if module:
                    tested_modules.add(module)
            
            # Look for module references in test names
            for test_func in test_functions:
                # Try to extract module name from test function name
                # e.g., test_generation_api -> generation_api
                match = re.search(r'test_(\w+_api|\w+_service|\w+_utils?)', test_func)
                if match:
                    tested_modules.add(match.group(1))
            
            return {
                'file_path': str(test_file.relative_to(self.root_dir)),
                'file_name': test_file.name,
                'test_functions': test_functions,
                'test_classes': test_classes,
                'tested_modules': list(tested_modules),
                'line_count': len(content.splitlines())
            }
            
        except Exception as e:
            return {
                'file_path': str(test_file.relative_to(self.root_dir)),
                'error': str(e)
            }
    
    def scan_test_files(self):
        """Scan all test files"""
        tests_dir = self.root_dir / 'backend' / 'tests'
        
        if not tests_dir.exists():
            print(f"Tests directory not found: {tests_dir}")
            return
        
        for test_file in tests_dir.rglob('test_*.py'):
            test_info = self.extract_test_info(test_file)
            if test_info:
                self.test_files.append(test_info)
        
        # Also check for files in test subdirectories
        for test_file in tests_dir.rglob('*.py'):
            if test_file.name.startswith('test_') or 'test' in test_file.parent.name.lower():
                if test_file not in [Path(t['file_path']) for t in self.test_files]:
                    test_info = self.extract_test_info(test_file)
                    if test_info:
                        self.test_files.append(test_info)
    
    def map_tests_to_components(self):
        """Map tests to components"""
        # Create module name to component mapping
        module_map = {}
        for comp in self.components:
            module_path = comp.get('module_path', '')
            file_name = comp.get('file_name', '')
            
            if module_path:
                # Map by full module path
                module_map[module_path] = comp
                
                # Map by last part of module path
                parts = module_path.split('.')
                if len(parts) > 1:
                    module_map[parts[-1]] = comp
                
                # Map by file name without extension
                if file_name:
                    base_name = file_name.replace('.py', '')
                    module_map[base_name] = comp
        
        # Match tests to components
        for test_info in self.test_files:
            test_path = test_info['file_path']
            tested_modules = test_info.get('tested_modules', [])
            
            matched_components = set()
            
            # Try to match by tested modules
            for module in tested_modules:
                # Try exact match
                if module in module_map:
                    comp = module_map[module]
                    matched_components.add(comp['file_path'])
                    self.component_to_test_map[comp['file_path']].append(test_path)
                
                # Try partial match
                for comp in self.components:
                    comp_module = comp.get('module_path', '')
                    comp_file = comp.get('file_name', '')
                    
                    if module in comp_module or module in comp_file:
                        matched_components.add(comp['file_path'])
                        self.component_to_test_map[comp['file_path']].append(test_path)
            
            # Also try to match by test file name
            test_name = Path(test_path).stem
            for comp in self.components:
                comp_file = comp.get('file_name', '').replace('.py', '')
                comp_module = comp.get('module_path', '').split('.')[-1]
                
                if comp_file in test_name or comp_module in test_name:
                    matched_components.add(comp['file_path'])
                    self.component_to_test_map[comp['file_path']].append(test_path)
            
            if matched_components:
                self.test_to_component_map[test_path] = list(matched_components)
    
    def identify_untested_components(self) -> List[Dict]:
        """Identify components without tests"""
        untested = []
        
        for comp in self.components:
            comp_path = comp.get('file_path', '')
            comp_type = comp.get('component_type', '')
            
            # Skip test files themselves
            if comp_type == 'test':
                continue
            
            # Skip configuration and application files (usually tested indirectly)
            if comp_type in ['configuration', 'application', 'celery']:
                continue
            
            # Check if component has tests
            if comp_path not in self.component_to_test_map:
                untested.append({
                    'file_path': comp_path,
                    'module_path': comp.get('module_path', ''),
                    'component_type': comp_type,
                    'classes': len(comp.get('classes', [])),
                    'functions': len(comp.get('functions', []))
                })
        
        return untested
    
    def get_test_statistics(self) -> Dict:
        """Get statistics about tests"""
        stats = {
            'total_test_files': len(self.test_files),
            'total_test_functions': sum(len(t.get('test_functions', [])) for t in self.test_files),
            'total_test_classes': sum(len(t.get('test_classes', [])) for t in self.test_files),
            'components_with_tests': len(self.component_to_test_map),
            'components_without_tests': len(self.identify_untested_components()),
            'tests_by_category': defaultdict(int)
        }
        
        # Categorize tests
        for test_info in self.test_files:
            test_path = test_info['file_path']
            if 'unit' in test_path:
                stats['tests_by_category']['unit'] += 1
            elif 'integration' in test_path:
                stats['tests_by_category']['integration'] += 1
            elif 'system' in test_path:
                stats['tests_by_category']['system'] += 1
            else:
                stats['tests_by_category']['other'] += 1
        
        return stats
    
    def save_results(self, output_file: str):
        """Save mapping results"""
        untested = self.identify_untested_components()
        stats = self.get_test_statistics()
        
        results = {
            'statistics': stats,
            'test_files': self.test_files,
            'test_to_component_map': dict(self.test_to_component_map),
            'component_to_test_map': {
                comp: tests 
                for comp, tests in self.component_to_test_map.items()
            },
            'untested_components': untested
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, default=str)
        
        print(f"Test mapping saved to {output_file}")
        print(f"\nTest Statistics:")
        print(f"  Total test files: {stats['total_test_files']}")
        print(f"  Total test functions: {stats['total_test_functions']}")
        print(f"  Components with tests: {stats['components_with_tests']}")
        print(f"  Components without tests: {stats['components_without_tests']}")
        print(f"\nTests by category:")
        for category, count in stats['tests_by_category'].items():
            print(f"  {category}: {count}")


def main():
    import sys
    
    scan_file = sys.argv[1] if len(sys.argv) > 1 else 'component_scan.json'
    root_dir = sys.argv[2] if len(sys.argv) > 2 else os.getcwd()
    output_file = sys.argv[3] if len(sys.argv) > 3 else 'test_mapping.json'
    
    if not os.path.exists(scan_file):
        print(f"Error: Scan file {scan_file} not found. Run scan_components.py first.")
        return 1
    
    mapper = TestMapper(scan_file, root_dir)
    mapper.scan_test_files()
    mapper.map_tests_to_components()
    mapper.save_results(output_file)
    
    return 0


if __name__ == '__main__':
    exit(main())

