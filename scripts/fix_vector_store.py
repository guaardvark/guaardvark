#!/usr/bin/env python3
"""
Fix Vector Store - Clean up mixed embedding dimensions

This script fixes the vector store when it has embeddings with inconsistent dimensions,
which causes "inhomogeneous shape" errors during search.

Usage:
    python3 scripts/fix_vector_store.py [--keep-dimension 384|4096] [--dry-run]

Options:
    --keep-dimension: Which embedding dimension to keep (default: most common)
    --dry-run: Show what would be done without making changes
"""

import json
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def analyze_vector_store(vector_store_path: str) -> dict:
    """Analyze the vector store and return statistics"""
    print(f"Analyzing: {vector_store_path}")

    with open(vector_store_path, "r") as f:
        data = json.load(f)

    if "embedding_dict" not in data:
        print("No embedding_dict found in vector store")
        return {"error": "No embedding_dict found"}

    embeddings = data["embedding_dict"]
    dimensions = {}

    for node_id, embedding in embeddings.items():
        if isinstance(embedding, list):
            dim = len(embedding)
            if dim not in dimensions:
                dimensions[dim] = []
            dimensions[dim].append(node_id)

    return {
        "total_embeddings": len(embeddings),
        "dimensions": {dim: len(ids) for dim, ids in dimensions.items()},
        "dimension_details": dimensions,
        "data": data,
    }


def fix_vector_store(
    vector_store_path: str, keep_dimension: int = None, dry_run: bool = False
):
    """Fix the vector store by removing embeddings with inconsistent dimensions"""

    analysis = analyze_vector_store(vector_store_path)

    if "error" in analysis:
        print(f"Error: {analysis['error']}")
        return False

    print(f"\nTotal embeddings: {analysis['total_embeddings']}")
    print("Embedding dimensions found:")
    for dim, count in sorted(analysis["dimensions"].items(), key=lambda x: -x[1]):
        pct = (count / analysis["total_embeddings"]) * 100
        print(f"  Dimension {dim}: {count} embeddings ({pct:.1f}%)")

    # Determine which dimension to keep
    if keep_dimension is None:
        # Keep the most common dimension
        keep_dimension = max(
            analysis["dimensions"].keys(), key=lambda d: analysis["dimensions"][d]
        )
        print(f"\nAuto-selected dimension to keep: {keep_dimension} (most common)")
    else:
        if keep_dimension not in analysis["dimensions"]:
            print(f"\nError: Dimension {keep_dimension} not found in vector store")
            return False
        print(f"\nKeeping dimension: {keep_dimension}")

    # Calculate what will be removed
    to_remove = []
    for dim, node_ids in analysis["dimension_details"].items():
        if dim != keep_dimension:
            to_remove.extend(node_ids)

    print(f"\nWill remove {len(to_remove)} embeddings with wrong dimensions")
    print(
        f"Will keep {analysis['dimensions'][keep_dimension]} embeddings with dimension {keep_dimension}"
    )

    if dry_run:
        print("\n[DRY RUN] No changes made")
        return True

    # Create backup
    backup_path = (
        f"{vector_store_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    print(f"\nCreating backup: {backup_path}")
    with open(backup_path, "w") as f:
        json.dump(analysis["data"], f)

    # Remove inconsistent embeddings
    data = analysis["data"]
    for node_id in to_remove:
        del data["embedding_dict"][node_id]

    # Also clean up node_dict and text_id_to_ref_doc_id if they exist
    if "node_dict" in data:
        for node_id in to_remove:
            if node_id in data["node_dict"]:
                del data["node_dict"][node_id]

    if "text_id_to_ref_doc_id" in data:
        for node_id in to_remove:
            if node_id in data["text_id_to_ref_doc_id"]:
                del data["text_id_to_ref_doc_id"][node_id]

    # Save cleaned vector store
    print(f"Saving cleaned vector store: {vector_store_path}")
    with open(vector_store_path, "w") as f:
        json.dump(data, f)

    print(f"\nDone! Removed {len(to_remove)} embeddings")
    print(f"Remaining embeddings: {len(data['embedding_dict'])}")
    print(f"\nBackup saved to: {backup_path}")
    print("\nRestart the application for changes to take effect.")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Fix vector store with mixed embedding dimensions"
    )
    parser.add_argument(
        "--keep-dimension", type=int, help="Dimension to keep (default: most common)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument("--vector-store", type=str, help="Path to vector store file")
    args = parser.parse_args()

    # Find vector store path
    if args.vector_store:
        vector_store_path = args.vector_store
    else:
        # Default paths to check
        possible_paths = [
            project_root / "data" / "default__vector_store.json",
            project_root / "data" / "storage" / "default__vector_store.json",
        ]

        vector_store_path = None
        for path in possible_paths:
            if path.exists():
                vector_store_path = str(path)
                break

        if not vector_store_path:
            print(
                "Could not find vector store file. Use --vector-store to specify path."
            )
            sys.exit(1)

    if not os.path.exists(vector_store_path):
        print(f"Vector store not found: {vector_store_path}")
        sys.exit(1)

    success = fix_vector_store(
        vector_store_path, keep_dimension=args.keep_dimension, dry_run=args.dry_run
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
