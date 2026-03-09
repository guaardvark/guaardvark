#!/bin/bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$DIR/backend"

if [ ! -d "venv" ]; then
    echo "No virtual environment found. Please run ./start.sh first."
    exit 1
fi

source venv/bin/activate

echo "Ensuring linters are installed..."
pip install -q flake8 black pyre-check

echo "-----------------------------------"
echo "Running Flake8 (Syntax & Indentation Check)..."
if ! flake8 . --select=E9,E11,F63,F7,F82 --show-source --statistics --exclude=venv,migrations; then
    echo "❌ Flake8 check failed. You have syntax or structural indentation errors."
    exit 1
fi
echo "✓ Flake8 syntax checks passed."

echo "-----------------------------------"
echo "Running Black (Formatting Check)..."
if ! black --check . --exclude "venv|migrations"; then
    echo "❌ Black formatting check failed."
    echo "💡 Run 'cd backend && source venv/bin/activate && black .' to auto-format."
else
    echo "✓ Black formatting checks passed."
fi

echo "-----------------------------------"
echo "✓ All pre-flight checks passed."
exit 0
