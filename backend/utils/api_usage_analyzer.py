#!/usr/bin/env python3
"""
Comprehensive API Usage Analyzer
Analyzes both backend blueprint registration and frontend API usage
to identify which APIs are actively used, internal-only, or potentially unused.
"""

import os
import re
from collections import defaultdict
from pathlib import Path

# Get project root
ROOT = Path(__file__).parent.parent.parent

def parse_backend_blueprints():
    """Parse backend log to get all registered blueprints with endpoints"""
    blueprints = {}
    log_file = ROOT / 'logs' / 'backend.log'
    
    if not log_file.exists():
        print(f"❌ Backend log not found: {log_file}")
        return blueprints
    
    with open(log_file, 'r') as f:
        for line in f:
            # Match format: - blueprint_name (/api/endpoint) from module
            match = re.search(r'-\s+(\w+)\s+\((/api/[^)]+)\)\s+from\s+([\w.]+)', line)
            if match:
                bp_name = match.group(1)
                endpoint = match.group(2).replace('/api/', '')
                module = match.group(3)
                blueprints[bp_name] = {
                    'endpoint': endpoint,
                    'module': module,
                    'frontend_calls': 0,
                    'files': []
                }
    
    return blueprints

def scan_frontend_usage():
    """Scan frontend for API endpoint usage"""
    api_usage = defaultdict(list)
    frontend_dir = ROOT / 'frontend' / 'src'
    
    if not frontend_dir.exists():
        print(f"❌ Frontend directory not found: {frontend_dir}")
        return api_usage
    
    for filepath in frontend_dir.rglob('*.js*'):
        if 'node_modules' in str(filepath):
            continue
        
        try:
            content = filepath.read_text(encoding='utf-8', errors='ignore')
            # Find API calls in various formats
            patterns = [
                r'[\'"`](/api/([a-z0-9-]+))',  # Direct API calls
                r'BASE_URL.*?[\'"`]/([a-z0-9-]+)',  # BASE_URL + endpoint
                r'fetch.*?[\'"`]/api/([a-z0-9-]+)',  # fetch calls
                r'axios.*?[\'"`]/api/([a-z0-9-]+)',  # axios calls
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, content)
                for match in matches:
                    endpoint = match if isinstance(match, str) else match[0]
                    endpoint = endpoint.replace('/api/', '')
                    rel_path = filepath.relative_to(frontend_dir)
                    if str(rel_path) not in api_usage[endpoint]:
                        api_usage[endpoint].append(str(rel_path))
        except Exception as e:
            pass
    
    return api_usage

def categorize_apis(blueprints, frontend_usage):
    """Categorize APIs based on usage and purpose"""
    categories = {
        'frontend_used': [],
        'internal_backend': [],
        'test_debug': [],
        'potentially_unused': []
    }
    
    for bp_name, bp_info in blueprints.items():
        endpoint = bp_info['endpoint']
        module_name = bp_info['module'].split('.')[-1].lower()
        
        # Check frontend usage (try multiple endpoint formats)
        possible_endpoints = [
            endpoint,
            endpoint.replace('-', '_'),
            endpoint.replace('_', '-'),
            bp_name.replace('_api', '').replace('_bp', ''),
            bp_name.replace('_api', '').replace('_bp', '').replace('_', '-')
        ]
        
        frontend_files = []
        for possible in possible_endpoints:
            if possible in frontend_usage:
                frontend_files.extend(frontend_usage[possible])
        
        if frontend_files:
            bp_info['frontend_calls'] = len(frontend_files)
            bp_info['files'] = list(set(frontend_files))
            categories['frontend_used'].append((bp_name, bp_info))
        
        # Categorize non-frontend APIs
        elif 'test' in module_name or 'test' in bp_name.lower():
            categories['test_debug'].append((bp_name, bp_info))
        elif 'debug' in module_name or 'debug' in bp_name.lower():
            categories['test_debug'].append((bp_name, bp_info))
        elif any(kw in module_name for kw in ['celery', 'monitor', 'system', 'meta', 'reboot', 'diagnostics']):
            categories['internal_backend'].append((bp_name, bp_info))
        else:
            categories['potentially_unused'].append((bp_name, bp_info))
    
    return categories

def print_results(categories, blueprints):
    """Print analysis results"""
    print("=" * 80)
    print("COMPREHENSIVE API USAGE ANALYSIS")
    print("=" * 80)
    
    # Frontend Used
    print(f"\n{'=' * 80}")
    print(f"✅ ACTIVELY USED BY FRONTEND ({len(categories['frontend_used'])} APIs)")
    print(f"{'=' * 80}")
    for bp_name, bp_info in sorted(categories['frontend_used'], key=lambda x: x[1]['frontend_calls'], reverse=True):
        print(f"\n{bp_name:30} /api/{bp_info['endpoint']}")
        print(f"{'':30} Used in {bp_info['frontend_calls']} file(s):")
        for f in bp_info['files'][:5]:
            print(f"{'':30}   • {f}")
        if len(bp_info['files']) > 5:
            print(f"{'':30}   ... and {len(bp_info['files'])-5} more")
    
    # Internal/Backend
    print(f"\n{'=' * 80}")
    print(f"⚙️  INTERNAL/BACKEND SERVICES ({len(categories['internal_backend'])} APIs)")
    print(f"{'=' * 80}")
    print("These are likely used by Celery, system monitoring, or internal services:\n")
    for bp_name, bp_info in sorted(categories['internal_backend']):
        print(f"  • {bp_name:30} /api/{bp_info['endpoint']:25}")
    
    # Test/Debug
    print(f"\n{'=' * 80}")
    print(f"🧪 TEST/DEBUG APIs ({len(categories['test_debug'])} APIs)")
    print(f"{'=' * 80}")
    print("These can likely be archived:\n")
    for bp_name, bp_info in sorted(categories['test_debug']):
        module_file = bp_info['module'].replace('backend.', '') + '.py'
        print(f"  • {bp_name:30} /api/{bp_info['endpoint']:25}")
        print(f"{'':34} → {module_file}")
    
    # Potentially Unused
    print(f"\n{'=' * 80}")
    print(f"❓ POTENTIALLY UNUSED ({len(categories['potentially_unused'])} APIs)")
    print(f"{'=' * 80}")
    print("Not called from frontend and not obvious internal services:\n")
    for bp_name, bp_info in sorted(categories['potentially_unused']):
        module_file = bp_info['module'].replace('backend.', '') + '.py'
        print(f"  • {bp_name:30} /api/{bp_info['endpoint']:25}")
        print(f"{'':34} → {module_file}")
    
    # Summary
    print(f"\n{'=' * 80}")
    print("SUMMARY & RECOMMENDATIONS")
    print(f"{'=' * 80}")
    total = len(blueprints)
    print(f"\nTotal Registered APIs: {total}")
    print(f"  ✅ Used by Frontend: {len(categories['frontend_used'])} ({len(categories['frontend_used'])/total*100:.1f}%)")
    print(f"  ⚙️  Internal/Backend: {len(categories['internal_backend'])} ({len(categories['internal_backend'])/total*100:.1f}%)")
    print(f"  🧪 Test/Debug: {len(categories['test_debug'])} ({len(categories['test_debug'])/total*100:.1f}%)")
    print(f"  ❓ Potentially Unused: {len(categories['potentially_unused'])} ({len(categories['potentially_unused'])/total*100:.1f}%)")
    
    print(f"\n{'=' * 80}")
    print("RECOMMENDED ACTIONS")
    print(f"{'=' * 80}")
    
    if categories['test_debug']:
        print("\n1. SAFE TO ARCHIVE (Test/Debug APIs):")
        print("   Move these to backend/api/_archive/:")
        for bp_name, bp_info in categories['test_debug']:
            module_file = bp_info['module'].split('.')[-1] + '.py'
            print(f"     • {module_file}")
    
    if categories['potentially_unused']:
        print("\n2. INVESTIGATE FURTHER (Potentially Unused):")
        print("   Before archiving, verify these are not used by:")
        print("     • Celery background tasks")
        print("     • External services or webhooks")
        print("     • Admin/maintenance scripts")
        print("     • Kept for backward compatibility")
        print(f"\n   {len(categories['potentially_unused'])} APIs need investigation")
    
    print(f"\n3. KEEP ({len(categories['frontend_used']) + len(categories['internal_backend'])} APIs):")
    print("   These are actively in use and should be maintained")

def main():
    """Main analysis function"""
    print("Starting comprehensive API usage analysis...\n")
    
    # Parse backend blueprints
    blueprints = parse_backend_blueprints()
    if not blueprints:
        print("❌ No blueprints found. Make sure backend is running and logs exist.")
        return
    
    print(f"✓ Found {len(blueprints)} registered blueprints")
    
    # Scan frontend usage
    frontend_usage = scan_frontend_usage()
    print(f"✓ Found {len(frontend_usage)} unique API endpoints called from frontend")
    
    # Categorize
    categories = categorize_apis(blueprints, frontend_usage)
    
    # Print results
    print_results(categories, blueprints)

if __name__ == '__main__':
    main()

