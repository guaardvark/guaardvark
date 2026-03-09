#!/usr/bin/env python3
"""Test script to verify light mode API performance improvements."""

import requests
import time
import sys

API_BASE = "http://localhost:5000/api/files"

def test_browse_mode(path, mode):
    """Test browse API with different modes."""
    url = f"{API_BASE}/browse?path={path}&fields={mode}"

    start = time.time()
    response = requests.get(url)
    elapsed = time.time() - start

    if response.status_code != 200:
        print(f"ERROR: {response.status_code} - {response.text}")
        return None

    data = response.json()
    content_length = len(response.content)

    return {
        'mode': mode,
        'time_ms': elapsed * 1000,
        'size_bytes': content_length,
        'size_kb': content_length / 1024,
        'document_count': len(data['data'].get('documents', [])),
        'folder_count': len(data['data'].get('folders', []))
    }

def format_results(light_result, full_result):
    """Format comparison results."""
    print("\n" + "="*80)
    print("LIGHT MODE vs FULL MODE PERFORMANCE COMPARISON")
    print("="*80)

    print(f"\nFolder: {light_result.get('path', 'root')}")
    print(f"Files: {light_result['document_count']}, Folders: {light_result['folder_count']}")

    print("\n" + "-"*80)
    print(f"{'Metric':<30} {'Light Mode':<20} {'Full Mode':<20} {'Improvement'}")
    print("-"*80)

    # Response time
    light_time = light_result['time_ms']
    full_time = full_result['time_ms']
    time_improvement = full_time / light_time if light_time > 0 else 0
    print(f"{'Response Time':<30} {light_time:>10.1f} ms      {full_time:>10.1f} ms      {time_improvement:.1f}x faster")

    # Response size
    light_size = light_result['size_kb']
    full_size = full_result['size_kb']
    size_improvement = full_size / light_size if light_size > 0 else 0
    print(f"{'Response Size':<30} {light_size:>10.1f} KB      {full_size:>10.1f} KB      {size_improvement:.1f}x smaller")

    print("-"*80)
    print(f"\n✓ Light mode is {time_improvement:.1f}x faster and uses {size_improvement:.1f}x less bandwidth")
    print("="*80 + "\n")

def main():
    # Test root folder
    path = sys.argv[1] if len(sys.argv) > 1 else "/"

    print(f"Testing API performance for path: {path}")
    print("This will test both light and full modes...\n")

    # Test light mode
    print("Testing light mode...")
    light_result = test_browse_mode(path, "light")
    if not light_result:
        print("Failed to get light mode results")
        return 1

    # Test full mode
    print("Testing full mode...")
    full_result = test_browse_mode(path, "full")
    if not full_result:
        print("Failed to get full mode results")
        return 1

    # Add path to results for display
    light_result['path'] = path
    full_result['path'] = path

    # Display comparison
    format_results(light_result, full_result)

    # Verify light mode data structure
    print("Verifying light mode data structure...")
    url = f"{API_BASE}/browse?path={path}&fields=light"
    response = requests.get(url)
    data = response.json()

    if data['data']['documents']:
        sample_doc = data['data']['documents'][0]
        expected_fields = {'id', 'filename', 'type', 'size', 'uploaded_at', 'index_status', 'path'}
        actual_fields = set(sample_doc.keys())

        print(f"Expected fields: {sorted(expected_fields)}")
        print(f"Actual fields:   {sorted(actual_fields)}")

        if expected_fields == actual_fields:
            print("✓ Light mode returns exactly the expected fields\n")
        else:
            print("⚠ Field mismatch detected!")
            print(f"  Missing: {expected_fields - actual_fields}")
            print(f"  Extra:   {actual_fields - expected_fields}\n")
    else:
        print("No documents in this folder to verify structure\n")

    return 0

if __name__ == "__main__":
    sys.exit(main())
