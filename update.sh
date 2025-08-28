#!/bin/bash

# Exit on error
set -e

# Get the script's directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Source and destination paths
SOURCE_SCRIPT="$SCRIPT_DIR/auto_commit.py"
DEST_DIR="$HOME/bin"
DEST_SCRIPT="$DEST_DIR/cmt"

# Create bin directory if it doesn't exist
if [ ! -d "$DEST_DIR" ]; then
    echo "Creating $DEST_DIR directory..."
    mkdir -p "$DEST_DIR"
fi

# Check if source file exists
if [ ! -f "$SOURCE_SCRIPT" ]; then
    echo "Error: $SOURCE_SCRIPT not found!"
    exit 1
fi

# Copy and make executable
echo "Copying auto_commit.py to $DEST_DIR..."
cp "$SOURCE_SCRIPT" "$DEST_SCRIPT"
chmod +x "$DEST_SCRIPT"

echo "Successfully installed cmt to $DEST_DIR"
echo "You can now run 'cmt' from anywhere in your terminal" 