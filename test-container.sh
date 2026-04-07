#!/bin/bash
# Quick launcher for Guaardvark test containers.
#
# Usage:
#   ./test-container.sh backups/my-release.zip          # test a release
#   ./test-container.sh backups/my-release.zip --gpu     # test with GPU
#   ./test-container.sh                                  # empty container
#
# First time: builds the base image (~2 min)
# After that: instant launch

IMAGE_NAME="gvark-test"
CONTAINER_FILE="Containerfile.test"
RELEASE_ZIP="$1"
GPU_FLAG=""

# Check for --gpu flag
for arg in "$@"; do
    if [ "$arg" = "--gpu" ]; then
        GPU_FLAG="--device nvidia.com/gpu=all --security-opt=label=disable"
    fi
done

# Build image if it doesn't exist
if ! podman image exists "$IMAGE_NAME" 2>/dev/null; then
    echo "Building test image (first time only)..."
    podman build -t "$IMAGE_NAME" -f "$CONTAINER_FILE" . || exit 1
    echo ""
fi

# Build the run command
RUN_CMD="podman run -it --rm"
RUN_CMD="$RUN_CMD -p 5000:5000 -p 5173:5173"
RUN_CMD="$RUN_CMD --hostname gvark-test"

# Mount release zip if provided
if [ -n "$RELEASE_ZIP" ] && [ "$RELEASE_ZIP" != "--gpu" ]; then
    RELEASE_ZIP=$(realpath "$RELEASE_ZIP")
    if [ ! -f "$RELEASE_ZIP" ]; then
        echo "Error: File not found: $RELEASE_ZIP"
        exit 1
    fi
    RUN_CMD="$RUN_CMD -v ${RELEASE_ZIP}:/tmp/release.zip:ro"
    echo "Testing release: $(basename "$RELEASE_ZIP")"
fi

# GPU support
if [ -n "$GPU_FLAG" ]; then
    RUN_CMD="$RUN_CMD $GPU_FLAG"
    echo "GPU: enabled"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Launch
$RUN_CMD "$IMAGE_NAME"
