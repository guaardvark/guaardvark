# ImagesPage Overhaul Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rebuild ImagesPage to match DocumentsPage quality with thumbnail previews, right-click context menus, folder organization, individual image management (cut/copy/paste), backed by unifying image storage into the Documents/Files system.

**Architecture:** Batch generation stays untouched. A new `register_batch_images()` service copies completed batch images into the Documents filesystem and creates DB records under `/Images/<batch-name>/`. The frontend ImagesPage is rebuilt to use the files API, reusing DocumentsPage components (FolderWindowWrapper, adapted context menus) with image-specific thumbnail rendering.

**Tech Stack:** Flask/SQLAlchemy (backend service), React/MUI/react-grid-layout (frontend), existing Files API endpoints, existing FolderWindowWrapper/DocumentsContextMenu components.

**Design doc:** `docs/plans/2026-02-28-images-page-overhaul-design.md`

---

## Task 1: Backend - Image Registration Service

Creates the core service that registers batch images into the Documents/Files system.

**Files:**
- Create: `backend/services/image_registration_service.py`
- Reference: `backend/models.py:667-880` (Folder + Document models)
- Reference: `backend/services/unified_upload_service.py` (pattern reference)
- Reference: `backend/api/files_api.py:28-33,117-122` (path helpers)
- Reference: `backend/config.py` (UPLOAD_DIR, OUTPUT_DIR paths)

**Step 1: Create the registration service**

```python
# backend/services/image_registration_service.py
"""
Service to register batch-generated images into the Documents/Files system.
Copies images from data/outputs/batch_images/ into data/uploads/Images/<batch-name>/
and creates Folder + Document DB records.
"""

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from flask import current_app

from backend.models import Folder, Document as DBDocument, db

logger = logging.getLogger(__name__)

IMAGES_ROOT_FOLDER_NAME = "Images"
IMAGES_ROOT_PATH = f"/{IMAGES_ROOT_FOLDER_NAME}"


def _get_upload_base() -> Path:
    """Get the base upload directory."""
    upload_folder = current_app.config.get("UPLOAD_FOLDER")
    if not upload_folder:
        raise ValueError("UPLOAD_FOLDER not configured")
    return Path(upload_folder)


def _ensure_images_root_folder() -> Folder:
    """Ensure the /Images root folder exists in DB and on disk."""
    folder = Folder.query.filter_by(path=IMAGES_ROOT_PATH).first()
    if not folder:
        folder = Folder(
            name=IMAGES_ROOT_FOLDER_NAME,
            path=IMAGES_ROOT_PATH,
            parent_id=None,
        )
        db.session.add(folder)
        db.session.flush()

        physical = _get_upload_base() / IMAGES_ROOT_FOLDER_NAME
        physical.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created /Images root folder (id={folder.id})")

    return folder


def _ensure_batch_folder(batch_name: str, parent: Folder) -> Folder:
    """Ensure a folder for the batch exists under /Images/."""
    folder_path = f"{IMAGES_ROOT_PATH}/{batch_name}"
    folder = Folder.query.filter_by(path=folder_path).first()
    if not folder:
        folder = Folder(
            name=batch_name,
            path=folder_path,
            parent_id=parent.id,
        )
        db.session.add(folder)
        db.session.flush()

        physical = _get_upload_base() / IMAGES_ROOT_FOLDER_NAME / batch_name
        physical.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created batch folder {folder_path} (id={folder.id})")

    return folder


def register_batch_images(
    batch_id: str,
    batch_output_dir: str,
    batch_name: Optional[str] = None,
) -> Tuple[Folder, List[DBDocument]]:
    """
    Register completed batch images into the Documents/Files system.

    Args:
        batch_id: The batch ID (e.g., 'batch_20260228_143700_a1b2c3d4')
        batch_output_dir: Absolute path to the batch output directory
        batch_name: Human-readable name for the folder (defaults to batch_id)

    Returns:
        Tuple of (batch_folder, list_of_document_records)
    """
    folder_name = batch_name or batch_id
    output_dir = Path(batch_output_dir)
    images_dir = output_dir / "images"
    thumbnails_dir = output_dir / "thumbnails"

    if not images_dir.exists():
        raise FileNotFoundError(f"Images directory not found: {images_dir}")

    # Ensure folder hierarchy
    root_folder = _ensure_images_root_folder()
    batch_folder = _ensure_batch_folder(folder_name, root_folder)

    # Destination paths
    upload_base = _get_upload_base()
    dest_dir = upload_base / IMAGES_ROOT_FOLDER_NAME / folder_name
    dest_thumbs_dir = dest_dir / "thumbnails"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_thumbs_dir.mkdir(parents=True, exist_ok=True)

    # Copy images and create Document records
    documents = []
    image_files = sorted(images_dir.glob("*"))

    for image_path in image_files:
        if not image_path.is_file():
            continue

        filename = image_path.name
        dest_path = dest_dir / filename
        relative_path = f"{IMAGES_ROOT_FOLDER_NAME}/{folder_name}/{filename}"

        # Skip if already registered (idempotent)
        existing = DBDocument.query.filter_by(path=relative_path).first()
        if existing:
            documents.append(existing)
            continue

        # Copy image file
        shutil.copy2(str(image_path), str(dest_path))

        # Copy thumbnail if it exists
        thumb_name = image_path.stem + ".jpg"
        thumb_src = thumbnails_dir / thumb_name
        if thumb_src.exists():
            shutil.copy2(str(thumb_src), str(dest_thumbs_dir / thumb_name))

        # Create Document record
        file_size = dest_path.stat().st_size
        file_ext = image_path.suffix.lower()

        doc = DBDocument(
            filename=filename,
            path=relative_path,
            type=file_ext,
            folder_id=batch_folder.id,
            size=file_size,
            index_status="NOT_INDEXED",
            is_code_file=False,
            file_metadata=json.dumps({
                "source": "batch_generation",
                "batch_id": batch_id,
                "has_thumbnail": thumb_src.exists(),
            }),
            uploaded_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.session.add(doc)
        documents.append(doc)

    db.session.commit()
    logger.info(
        f"Registered {len(documents)} images from batch {batch_id} "
        f"into {batch_folder.path}"
    )

    return batch_folder, documents
```

**Step 2: Verify service imports work**

Run: `cd /home/llamax1/LLAMAX7 && python3 -c "from backend.services.image_registration_service import register_batch_images; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add backend/services/image_registration_service.py
git commit -m "feat: add image registration service for batch-to-documents integration"
```

---

## Task 2: Backend - Thumbnail Serving Endpoint

Add an endpoint to serve image thumbnails from the Documents filesystem, since thumbnails are stored in a `thumbnails/` subdirectory alongside the images.

**Files:**
- Modify: `backend/api/files_api.py` (add thumbnail route)

**Step 1: Add thumbnail endpoint to files_api.py**

Add after the existing download endpoint in `files_api.py`:

```python
@files_bp.route("/thumbnail", methods=["GET"])
@ensure_db_session_cleanup
def get_thumbnail():
    """GET /api/files/thumbnail?path=<document_path> - Get thumbnail for an image document"""
    doc_path = request.args.get("path", "").strip()
    if not doc_path:
        return jsonify({"error": "path parameter required"}), 400

    if not ensure_path_is_safe(doc_path):
        return jsonify({"error": "Invalid path"}), 400

    base = get_upload_base_path()
    doc_physical = base / doc_path.lstrip("/")

    if not doc_physical.exists():
        return jsonify({"error": "File not found"}), 404

    # Look for thumbnail in thumbnails/ subdirectory
    parent_dir = doc_physical.parent
    thumb_dir = parent_dir / "thumbnails"
    thumb_name = doc_physical.stem + ".jpg"
    thumb_path = thumb_dir / thumb_name

    if thumb_path.exists():
        return send_file(str(thumb_path), mimetype="image/jpeg")

    # Fallback: serve the original image
    return send_file(str(doc_physical))
```

**Step 2: Verify endpoint loads**

Run: `cd /home/llamax1/LLAMAX7 && python3 -c "from backend.api.files_api import files_bp; print('Routes:', [r.rule for r in files_bp.deferred_functions] if hasattr(files_bp, 'deferred_functions') else 'OK')"`

**Step 3: Commit**

```bash
git add backend/api/files_api.py
git commit -m "feat: add thumbnail serving endpoint for image documents"
```

---

## Task 3: Backend - Hook Batch Completion to Registration

Call `register_batch_images()` when a batch finishes generating.

**Files:**
- Modify: `backend/services/batch_image_generator.py:488-516` (completion handler)

**Step 1: Add registration call after batch completion**

In `batch_image_generator.py`, after line 492 (`self._save_batch_metadata(batch_status, output_dir)`), add the registration hook:

```python
                # Register images into Documents/Files system
                if batch_status.status == "completed" and batch_status.completed_images > 0:
                    try:
                        from backend.services.image_registration_service import register_batch_images
                        # Use batch name from metadata if available, else batch_id
                        batch_name = getattr(request, 'batch_name', None) or batch_id
                        register_batch_images(
                            batch_id=batch_id,
                            batch_output_dir=str(output_dir),
                            batch_name=batch_name,
                        )
                        logger.info(f"Registered batch {batch_id} images into Documents system")
                    except Exception as reg_err:
                        logger.error(f"Failed to register batch images: {reg_err}")
                        # Don't fail the batch if registration fails
```

**Important:** This runs inside a thread (`run_batch`). The Flask app context must be available. Check if the thread already has app context pushed - it likely does since the batch generator is instantiated within a request context. If not, wrap the call in `with app.app_context():`.

**Step 2: Verify the hook location**

Read `batch_image_generator.py` lines 488-520 to confirm the edit is in the right place.

**Step 3: Commit**

```bash
git add backend/services/batch_image_generator.py
git commit -m "feat: hook batch completion to image registration service"
```

---

## Task 4: Backend - Migration Script for Existing Batches

Create a script that scans existing batch directories and registers them into the Documents system.

**Files:**
- Create: `scripts/migrate_batch_images.py`

**Step 1: Create migration script**

```python
#!/usr/bin/env python3
"""
Migrate existing batch images into the Documents/Files system.
Scans data/outputs/batch_images/ for completed batches and registers them.

Usage:
    python3 scripts/migrate_batch_images.py [--dry-run]
"""

import json
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

os.environ.setdefault("GUAARDVARK_ROOT", str(project_root))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Migrate batch images to Documents system")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")
    args = parser.parse_args()

    from backend.app import create_app
    app = create_app()

    with app.app_context():
        from backend.services.image_registration_service import register_batch_images

        batch_base = project_root / "data" / "outputs" / "batch_images"
        if not batch_base.exists():
            print("No batch images directory found. Nothing to migrate.")
            return

        batch_dirs = sorted([
            d for d in batch_base.iterdir()
            if d.is_dir() and not d.name.startswith("_")
        ])

        print(f"Found {len(batch_dirs)} batch directories")

        migrated = 0
        skipped = 0
        errors = 0

        for batch_dir in batch_dirs:
            batch_id = batch_dir.name
            images_dir = batch_dir / "images"

            if not images_dir.exists() or not any(images_dir.iterdir()):
                print(f"  SKIP {batch_id}: no images")
                skipped += 1
                continue

            # Try to get batch name from metadata
            metadata_file = batch_dir / "batch_metadata.json"
            batch_name = batch_id
            if metadata_file.exists():
                try:
                    meta = json.loads(metadata_file.read_text())
                    batch_name = meta.get("batch_name", batch_id)
                except Exception:
                    pass

            image_count = len(list(images_dir.glob("*")))

            if args.dry_run:
                print(f"  WOULD MIGRATE {batch_id} ({image_count} images) -> /Images/{batch_name}/")
                migrated += 1
                continue

            try:
                folder, docs = register_batch_images(
                    batch_id=batch_id,
                    batch_output_dir=str(batch_dir),
                    batch_name=batch_name,
                )
                print(f"  MIGRATED {batch_id} -> {folder.path} ({len(docs)} images)")
                migrated += 1
            except Exception as e:
                print(f"  ERROR {batch_id}: {e}")
                errors += 1

        print(f"\nDone: {migrated} migrated, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
```

**Step 2: Test with --dry-run**

Run: `cd /home/llamax1/LLAMAX7 && python3 scripts/migrate_batch_images.py --dry-run`
Expected: Lists batch directories that would be migrated (or "Nothing to migrate" if no batches exist)

**Step 3: Run actual migration**

Run: `cd /home/llamax1/LLAMAX7 && python3 scripts/migrate_batch_images.py`

**Step 4: Commit**

```bash
git add scripts/migrate_batch_images.py
git commit -m "feat: add migration script for existing batch images"
```

---

## Task 5: Frontend - ImagesContextMenu Component

Create a context menu component for the ImagesPage, adapted from DocumentsContextMenu.

**Files:**
- Create: `frontend/src/components/images/ImagesContextMenu.jsx`
- Reference: `frontend/src/components/documents/DocumentsContextMenu.jsx` (template)

**Step 1: Create the context menu component**

```jsx
// frontend/src/components/images/ImagesContextMenu.jsx
// Context menu for ImagesPage - adapted from DocumentsContextMenu
// Supports: desktop, folder, image context types

import React from 'react';
import { Menu, MenuItem, ListItemIcon, ListItemText, Divider, Box, Tooltip } from '@mui/material';
import {
  CreateNewFolder as NewFolderIcon,
  ContentCut as CutIcon,
  ContentCopy as CopyIcon,
  ContentPaste as PasteIcon,
  Delete as DeleteIcon,
  DriveFileRenameOutline as RenameIcon,
  Download as DownloadIcon,
  SelectAll as SelectAllIcon,
  Sort as SortIcon,
  ViewModule as ArrangeIcon,
  Fullscreen as ViewIcon,
  Check as CheckIcon,
} from '@mui/icons-material';

const FOLDER_COLORS = [
  '#90CAF9', '#A5D6A7', '#FFCC80', '#EF9A9A',
  '#CE93D8', '#80DEEA', '#FFAB91', '#B0BEC5',
];

const ImagesContextMenu = ({
  anchorPosition,
  onClose,
  onNewFolder,
  onCut,
  onCopy,
  onPaste,
  onDelete,
  onRename,
  onDownload,
  onViewFullSize,
  onColorChange,
  onSelectAll,
  onSortBy,
  onArrangeIcons,
  onArrangeWindows,
  hasClipboard = false,
  hasSelection = false,
  contextType = 'desktop',
  selectedItem = null,
  folderColor = null,
}) => {
  if (!anchorPosition) return null;

  const handleSortBy = (field) => {
    onSortBy?.(field);
    onClose();
  };

  return (
    <Menu
      open={Boolean(anchorPosition)}
      onClose={onClose}
      anchorReference="anchorPosition"
      anchorPosition={anchorPosition}
      slotProps={{ paper: { sx: { minWidth: 200, maxWidth: 280 } } }}
    >
      {/* Desktop context */}
      {contextType === 'desktop' && [
        <MenuItem key="new-folder" onClick={() => { onNewFolder?.(); onClose(); }}>
          <ListItemIcon><NewFolderIcon fontSize="small" /></ListItemIcon>
          <ListItemText>New Folder</ListItemText>
        </MenuItem>,
        <MenuItem key="select-all" onClick={() => { onSelectAll?.(); onClose(); }}>
          <ListItemIcon><SelectAllIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Select All</ListItemText>
        </MenuItem>,
        <Divider key="d1" />,
        <MenuItem key="sort-name" onClick={() => handleSortBy('name')}>
          <ListItemIcon><SortIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Sort by Name</ListItemText>
        </MenuItem>,
        <MenuItem key="sort-date" onClick={() => handleSortBy('date')}>
          <ListItemIcon><SortIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Sort by Date</ListItemText>
        </MenuItem>,
        <MenuItem key="sort-size" onClick={() => handleSortBy('size')}>
          <ListItemIcon><SortIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Sort by Size</ListItemText>
        </MenuItem>,
        <Divider key="d2" />,
        <MenuItem key="arrange-icons" onClick={() => { onArrangeIcons?.(); onClose(); }}>
          <ListItemIcon><ArrangeIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Arrange Icons</ListItemText>
        </MenuItem>,
        <MenuItem key="arrange-windows" onClick={() => { onArrangeWindows?.(); onClose(); }}>
          <ListItemIcon><ArrangeIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Arrange Windows</ListItemText>
        </MenuItem>,
        hasClipboard && (
          <span key="paste-wrapper">
            <Divider />
            <MenuItem onClick={() => { onPaste?.(); onClose(); }}>
              <ListItemIcon><PasteIcon fontSize="small" /></ListItemIcon>
              <ListItemText>Paste</ListItemText>
            </MenuItem>
          </span>
        ),
      ]}

      {/* Folder context */}
      {(contextType === 'folder' || contextType === 'folder-window') && [
        <MenuItem key="cut" onClick={() => { onCut?.(); onClose(); }}>
          <ListItemIcon><CutIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Cut</ListItemText>
        </MenuItem>,
        <MenuItem key="copy" onClick={() => { onCopy?.(); onClose(); }}>
          <ListItemIcon><CopyIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Copy</ListItemText>
        </MenuItem>,
        hasClipboard && (
          <MenuItem key="paste" onClick={() => { onPaste?.(); onClose(); }}>
            <ListItemIcon><PasteIcon fontSize="small" /></ListItemIcon>
            <ListItemText>Paste</ListItemText>
          </MenuItem>
        ),
        <Divider key="d1" />,
        <MenuItem key="color" disabled sx={{ '&.Mui-disabled': { opacity: 1 } }}>
          <ListItemText sx={{ mr: 1 }}>Color</ListItemText>
          <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
            {FOLDER_COLORS.map((color) => (
              <Tooltip key={color} title={color}>
                <Box
                  onClick={(e) => { e.stopPropagation(); onColorChange?.(color); onClose(); }}
                  sx={{
                    width: 20, height: 20, borderRadius: '50%',
                    backgroundColor: color, cursor: 'pointer',
                    border: folderColor === color ? '2px solid white' : '1px solid rgba(255,255,255,0.3)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    '&:hover': { transform: 'scale(1.2)' },
                  }}
                >
                  {folderColor === color && <CheckIcon sx={{ fontSize: 12, color: 'white' }} />}
                </Box>
              </Tooltip>
            ))}
          </Box>
        </MenuItem>,
        <Divider key="d2" />,
        <MenuItem key="rename" onClick={() => { onRename?.(); onClose(); }}>
          <ListItemIcon><RenameIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Rename</ListItemText>
        </MenuItem>,
        <MenuItem key="delete" onClick={() => { onDelete?.(); onClose(); }}>
          <ListItemIcon><DeleteIcon fontSize="small" color="error" /></ListItemIcon>
          <ListItemText sx={{ color: 'error.main' }}>Delete</ListItemText>
        </MenuItem>,
      ]}

      {/* Image context */}
      {contextType === 'image' && [
        <MenuItem key="view" onClick={() => { onViewFullSize?.(); onClose(); }}>
          <ListItemIcon><ViewIcon fontSize="small" /></ListItemIcon>
          <ListItemText>View Full Size</ListItemText>
        </MenuItem>,
        <Divider key="d0" />,
        <MenuItem key="cut" onClick={() => { onCut?.(); onClose(); }}>
          <ListItemIcon><CutIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Cut</ListItemText>
        </MenuItem>,
        <MenuItem key="copy" onClick={() => { onCopy?.(); onClose(); }}>
          <ListItemIcon><CopyIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Copy</ListItemText>
        </MenuItem>,
        hasClipboard && (
          <MenuItem key="paste" onClick={() => { onPaste?.(); onClose(); }}>
            <ListItemIcon><PasteIcon fontSize="small" /></ListItemIcon>
            <ListItemText>Paste</ListItemText>
          </MenuItem>
        ),
        <Divider key="d1" />,
        <MenuItem key="download" onClick={() => { onDownload?.(); onClose(); }}>
          <ListItemIcon><DownloadIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Download</ListItemText>
        </MenuItem>,
        <MenuItem key="rename" onClick={() => { onRename?.(); onClose(); }}>
          <ListItemIcon><RenameIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Rename</ListItemText>
        </MenuItem>,
        <MenuItem key="delete" onClick={() => { onDelete?.(); onClose(); }}>
          <ListItemIcon><DeleteIcon fontSize="small" color="error" /></ListItemIcon>
          <ListItemText sx={{ color: 'error.main' }}>Delete</ListItemText>
        </MenuItem>,
      ]}
    </Menu>
  );
};

export default ImagesContextMenu;
```

**Step 2: Commit**

```bash
git add frontend/src/components/images/ImagesContextMenu.jsx
git commit -m "feat: add ImagesContextMenu component with desktop/folder/image contexts"
```

---

## Task 6: Frontend - ImageThumbnailGrid Component

Create a grid component that displays images as thumbnails inside folder windows. This replaces the list-based FolderContents for image folders.

**Files:**
- Create: `frontend/src/components/images/ImageThumbnailGrid.jsx`
- Reference: `frontend/src/components/documents/FolderContents.jsx:43-100` (props pattern)
- Reference: `frontend/src/components/images/ImageBatchContents.jsx` (thumbnail rendering pattern)

**Step 1: Create thumbnail grid component**

```jsx
// frontend/src/components/images/ImageThumbnailGrid.jsx
// Displays folder contents as a thumbnail grid with selection, drag, and context menu support

import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  Box, Typography, CircularProgress, IconButton, Tooltip, useTheme,
} from '@mui/material';
import { Folder as FolderIcon, BrokenImage as BrokenImageIcon } from '@mui/icons-material';
import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_URL || '';
const THUMB_SIZE = 160;
const GRID_GAP = 8;

const ImageThumbnailGrid = ({
  folder,
  currentPath,
  onNavigateToPath,
  viewMode = 'grid',
  selectedItems = new Set(),
  onSelectionChange,
  onContextMenu,
  onDragStart,
  onFolderOpen,
  onDrop,
  refreshKey = 0,
}) => {
  const theme = useTheme();
  const [items, setItems] = useState({ folders: [], files: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const containerRef = useRef(null);
  const lastClickedIndex = useRef(null);

  // Fetch folder contents
  const fetchContents = useCallback(async () => {
    if (!currentPath) return;
    setLoading(true);
    setError(null);
    try {
      const res = await axios.get(`${API_BASE}/api/files/browse`, {
        params: { path: currentPath, fields: 'light', limit: 500 },
      });
      const data = res.data;
      setItems({
        folders: data.folders || [],
        files: (data.files || data.documents || []),
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [currentPath]);

  useEffect(() => { fetchContents(); }, [fetchContents, refreshKey]);

  // All items combined for selection indexing
  const allItems = useMemo(() => [
    ...items.folders.map(f => ({ ...f, itemType: 'folder', key: `folder-${f.id}` })),
    ...items.files.map(f => ({ ...f, itemType: 'file', key: `file-${f.id}` })),
  ], [items]);

  const handleItemClick = useCallback((e, item, index) => {
    e.stopPropagation();
    const key = item.key;

    if (e.shiftKey && lastClickedIndex.current !== null) {
      // Range select
      const start = Math.min(lastClickedIndex.current, index);
      const end = Math.max(lastClickedIndex.current, index);
      const newSelection = new Set(selectedItems);
      for (let i = start; i <= end; i++) {
        newSelection.add(allItems[i].key);
      }
      onSelectionChange?.(newSelection);
    } else if (e.ctrlKey || e.metaKey) {
      // Toggle select
      const newSelection = new Set(selectedItems);
      if (newSelection.has(key)) newSelection.delete(key);
      else newSelection.add(key);
      onSelectionChange?.(newSelection);
    } else {
      // Single select
      onSelectionChange?.(new Set([key]));
    }
    lastClickedIndex.current = index;
  }, [selectedItems, onSelectionChange, allItems]);

  const handleItemDoubleClick = useCallback((e, item) => {
    e.stopPropagation();
    if (item.itemType === 'folder') {
      onNavigateToPath?.(item.path);
    }
  }, [onNavigateToPath]);

  const handleItemContextMenu = useCallback((e, item) => {
    e.preventDefault();
    e.stopPropagation();
    // Auto-select if not already selected
    if (!selectedItems.has(item.key)) {
      onSelectionChange?.(new Set([item.key]));
    }
    onContextMenu?.(e, item, item.itemType === 'folder' ? 'folder' : 'image');
  }, [selectedItems, onSelectionChange, onContextMenu]);

  const handleBackgroundContextMenu = useCallback((e) => {
    if (e.target === containerRef.current || e.target.dataset?.background) {
      e.preventDefault();
      onSelectionChange?.(new Set());
      onContextMenu?.(e, null, 'folder-window');
    }
  }, [onContextMenu, onSelectionChange]);

  const handleDragStart = useCallback((e, item) => {
    const dragItems = selectedItems.has(item.key)
      ? allItems.filter(i => selectedItems.has(i.key))
      : [item];
    e.dataTransfer.setData('application/json', JSON.stringify(
      dragItems.map(i => ({ id: i.id, type: i.itemType, path: i.path, name: i.name || i.filename }))
    ));
    e.dataTransfer.effectAllowed = 'move';
    onDragStart?.(e, item);
  }, [selectedItems, allItems, onDragStart]);

  const handleContainerDrop = useCallback((e) => {
    e.preventDefault();
    onDrop?.(e);
  }, [onDrop]);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  }, []);

  // Clear selection on background click
  const handleBackgroundClick = useCallback((e) => {
    if (e.target === containerRef.current || e.target.dataset?.background) {
      onSelectionChange?.(new Set());
      lastClickedIndex.current = null;
    }
  }, [onSelectionChange]);

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', minHeight: 200 }}>
        <CircularProgress size={32} />
      </Box>
    );
  }

  if (error) {
    return (
      <Box sx={{ p: 2, textAlign: 'center' }}>
        <Typography color="error">{error}</Typography>
      </Box>
    );
  }

  return (
    <Box
      ref={containerRef}
      data-background="true"
      onClick={handleBackgroundClick}
      onContextMenu={handleBackgroundContextMenu}
      onDrop={handleContainerDrop}
      onDragOver={handleDragOver}
      sx={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: `${GRID_GAP}px`,
        p: 1,
        minHeight: 200,
        alignContent: 'flex-start',
        overflow: 'auto',
        height: '100%',
      }}
    >
      {allItems.length === 0 && (
        <Box data-background="true" sx={{ width: '100%', textAlign: 'center', py: 4 }}>
          <Typography color="text.secondary" data-background="true">Empty folder</Typography>
        </Box>
      )}

      {allItems.map((item, index) => {
        const isSelected = selectedItems.has(item.key);
        const isFolder = item.itemType === 'folder';

        return (
          <Box
            key={item.key}
            draggable
            onDragStart={(e) => handleDragStart(e, item)}
            onClick={(e) => handleItemClick(e, item, index)}
            onDoubleClick={(e) => handleItemDoubleClick(e, item)}
            onContextMenu={(e) => handleItemContextMenu(e, item)}
            sx={{
              width: THUMB_SIZE,
              cursor: 'pointer',
              borderRadius: 1,
              border: isSelected
                ? `2px solid ${theme.palette.primary.main}`
                : '2px solid transparent',
              backgroundColor: isSelected
                ? theme.palette.action.selected
                : 'transparent',
              '&:hover': {
                backgroundColor: theme.palette.action.hover,
              },
              p: 0.5,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              userSelect: 'none',
            }}
          >
            {isFolder ? (
              <FolderIcon sx={{ fontSize: 64, color: item.color || '#90CAF9' }} />
            ) : (
              <Box
                sx={{
                  width: THUMB_SIZE - 8,
                  height: THUMB_SIZE - 8,
                  borderRadius: 0.5,
                  overflow: 'hidden',
                  backgroundColor: theme.palette.action.hover,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  border: `1px solid ${theme.palette.divider}`,
                }}
              >
                <img
                  src={`${API_BASE}/api/files/thumbnail?path=${encodeURIComponent(item.path)}`}
                  alt={item.filename}
                  loading="lazy"
                  style={{
                    maxWidth: '100%',
                    maxHeight: '100%',
                    objectFit: 'cover',
                    width: '100%',
                    height: '100%',
                  }}
                  onError={(e) => {
                    e.target.style.display = 'none';
                    e.target.nextSibling && (e.target.nextSibling.style.display = 'flex');
                  }}
                />
                <Box sx={{ display: 'none', alignItems: 'center', justifyContent: 'center', width: '100%', height: '100%' }}>
                  <BrokenImageIcon sx={{ fontSize: 32, color: 'text.secondary' }} />
                </Box>
              </Box>
            )}
            <Typography
              variant="caption"
              sx={{
                mt: 0.5,
                textAlign: 'center',
                width: '100%',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                fontSize: '0.7rem',
              }}
            >
              {isFolder ? item.name : item.filename}
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
};

export default ImageThumbnailGrid;
```

**Step 2: Commit**

```bash
git add frontend/src/components/images/ImageThumbnailGrid.jsx
git commit -m "feat: add ImageThumbnailGrid component for folder window contents"
```

---

## Task 7: Frontend - ImageLightbox Component

Extract the lightbox viewer from ImageBatchContents into a reusable component.

**Files:**
- Create: `frontend/src/components/images/ImageLightbox.jsx`
- Reference: `frontend/src/components/images/ImageBatchContents.jsx:100-132` (existing lightbox logic)

**Step 1: Create lightbox component**

```jsx
// frontend/src/components/images/ImageLightbox.jsx
// Full-screen image lightbox with keyboard navigation

import React, { useEffect, useCallback } from 'react';
import { Box, IconButton, Typography, useTheme } from '@mui/material';
import {
  Close as CloseIcon,
  ArrowBack as PrevIcon,
  ArrowForward as NextIcon,
  Download as DownloadIcon,
} from '@mui/icons-material';

const ImageLightbox = ({
  imageUrl,
  imageName = '',
  onClose,
  onPrev,
  onNext,
  onDownload,
  hasPrev = false,
  hasNext = false,
}) => {
  const theme = useTheme();

  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Escape') onClose?.();
    else if (e.key === 'ArrowLeft' && hasPrev) onPrev?.();
    else if (e.key === 'ArrowRight' && hasNext) onNext?.();
  }, [onClose, onPrev, onNext, hasPrev, hasNext]);

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  if (!imageUrl) return null;

  return (
    <Box
      onClick={onClose}
      sx={{
        position: 'fixed',
        top: 0, left: 0, right: 0, bottom: 0,
        backgroundColor: 'rgba(0,0,0,0.9)',
        zIndex: 9999,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        cursor: 'pointer',
      }}
    >
      {/* Top bar */}
      <Box
        onClick={(e) => e.stopPropagation()}
        sx={{
          position: 'absolute', top: 0, left: 0, right: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          p: 1, background: 'linear-gradient(rgba(0,0,0,0.7), transparent)',
        }}
      >
        <Typography variant="body2" sx={{ color: 'white', ml: 1, opacity: 0.8 }}>
          {imageName}
        </Typography>
        <Box>
          {onDownload && (
            <IconButton onClick={onDownload} sx={{ color: 'white' }}>
              <DownloadIcon />
            </IconButton>
          )}
          <IconButton onClick={onClose} sx={{ color: 'white' }}>
            <CloseIcon />
          </IconButton>
        </Box>
      </Box>

      {/* Navigation arrows */}
      {hasPrev && (
        <IconButton
          onClick={(e) => { e.stopPropagation(); onPrev?.(); }}
          sx={{
            position: 'absolute', left: 16, color: 'white',
            backgroundColor: 'rgba(0,0,0,0.5)',
            '&:hover': { backgroundColor: 'rgba(0,0,0,0.7)' },
          }}
        >
          <PrevIcon fontSize="large" />
        </IconButton>
      )}
      {hasNext && (
        <IconButton
          onClick={(e) => { e.stopPropagation(); onNext?.(); }}
          sx={{
            position: 'absolute', right: 16, color: 'white',
            backgroundColor: 'rgba(0,0,0,0.5)',
            '&:hover': { backgroundColor: 'rgba(0,0,0,0.7)' },
          }}
        >
          <NextIcon fontSize="large" />
        </IconButton>
      )}

      {/* Image */}
      <img
        src={imageUrl}
        alt={imageName}
        onClick={(e) => e.stopPropagation()}
        style={{
          maxWidth: '90vw',
          maxHeight: '90vh',
          objectFit: 'contain',
          cursor: 'default',
        }}
      />
    </Box>
  );
};

export default ImageLightbox;
```

**Step 2: Commit**

```bash
git add frontend/src/components/images/ImageLightbox.jsx
git commit -m "feat: add ImageLightbox component for full-screen image viewing"
```

---

## Task 8: Frontend - Rewrite ImagesPage (Core Structure)

Replace the batch-based ImagesPage with a Documents/Files-backed implementation. This is the largest task - the full page rewrite.

**Files:**
- Modify: `frontend/src/pages/ImagesPage.jsx` (full rewrite)
- Reference: `frontend/src/pages/DocumentsPage.jsx` (pattern source)
- Reference: `frontend/src/components/documents/FolderWindowWrapper.jsx` (reused)

**Step 1: Rewrite ImagesPage**

The new ImagesPage follows DocumentsPage patterns but is scoped to `/Images/` and uses thumbnail grid rendering. Key structural elements:

- **Imports**: React, MUI, react-grid-layout, FolderWindowWrapper, ImageThumbnailGrid, ImagesContextMenu, ImageLightbox, BatchImageGeneratorPage
- **Constants**: Same grid constants as DocumentsPage (WINDOWS_COLS=48, etc.)
- **State**: Same window management pattern (windows, windowLayout, windowColors, windowZIndex, iconPositions, clipboard, contextMenu)
- **Root path**: All files API calls scoped to `/Images/`
- **Data fetching**: `GET /api/files/browse?path=/Images/` for root folders and files
- **Window management**: Reuse exact same expand/close/minimize/z-index/arrange logic from DocumentsPage
- **Desktop icons**: Folders as Lucide `<Folder>` icons, images as thumbnails with border frame
- **Context menu**: ImagesContextMenu with desktop/folder/image types
- **Clipboard**: Cut/copy/paste using files API move/copy endpoints
- **Lightbox**: ImageLightbox for full-size viewing
- **Tabs**: Keep "Image Library" + "Image Gen" tab structure
- **State persistence**: Save/load via `/api/state/images-windows`

**Key differences from DocumentsPage:**
- Root is `/Images/` not `/`
- No upload button/action
- Default view mode is `grid` (thumbnails) not `list`
- Desktop icons for images show thumbnails instead of file type icons
- ImageThumbnailGrid instead of FolderContents in windows

The full implementation should be ~1200-1500 lines, closely following DocumentsPage structure. The implementing agent should:
1. Read DocumentsPage.jsx in full as the template
2. Adapt it with the changes listed above
3. Replace all `/api/files/browse?path=/` calls with `/api/files/browse?path=/Images/`
4. Replace FolderContents with ImageThumbnailGrid
5. Replace DocumentsContextMenu with ImagesContextMenu
6. Add ImageLightbox integration
7. Keep the BatchImageGeneratorPage tab

**Step 2: Verify it compiles**

Run: `cd /home/llamax1/LLAMAX7/frontend && npm run build 2>&1 | tail -20`

**Step 3: Commit**

```bash
git add frontend/src/pages/ImagesPage.jsx
git commit -m "feat: rewrite ImagesPage with Documents/Files integration and thumbnail grid"
```

---

## Task 9: Frontend - Integration Polish

Wire up remaining details: lightbox navigation across images in a folder, folder window breadcrumbs, ensure drag-and-drop between windows works.

**Files:**
- Modify: `frontend/src/pages/ImagesPage.jsx` (polish handlers)
- Modify: `frontend/src/components/images/ImageThumbnailGrid.jsx` (add lightbox trigger)

**Step 1: Add lightbox integration to ImageThumbnailGrid**

Add an `onImageDoubleClick` prop to ImageThumbnailGrid that fires when an image (not folder) is double-clicked. In ImagesPage, this opens the lightbox with the clicked image URL and enables prev/next navigation across the folder's images.

**Step 2: Ensure move operations work**

The cut/paste flow should:
1. Store selected items in clipboard state: `{ items: [{id, type, path, name}], operation: 'cut' }`
2. On paste: call `POST /api/files/move` for each item to the target folder path
3. Refresh both source and target folder windows
4. Clear clipboard after paste

**Step 3: Verify full flow manually**

1. Start the app: `./start.sh --fast`
2. Navigate to Images page
3. Verify folders display with folder icons
4. Open a folder window, verify thumbnails render
5. Right-click desktop background → context menu appears
6. Right-click a folder → folder context menu with colors
7. Right-click an image → image context menu
8. Double-click image → lightbox opens
9. Select multiple images → cut → navigate to another folder → paste

**Step 4: Commit**

```bash
git add frontend/src/pages/ImagesPage.jsx frontend/src/components/images/ImageThumbnailGrid.jsx
git commit -m "feat: polish ImagesPage with lightbox navigation and drag-and-drop"
```

---

## Task 10: End-to-End Verification

Verify the full pipeline: generate a batch → images appear in ImagesPage → organize into folders.

**Files:**
- No new files

**Step 1: Run migration on existing batches (if any)**

Run: `cd /home/llamax1/LLAMAX7 && python3 scripts/migrate_batch_images.py`

**Step 2: Start the application**

Run: `cd /home/llamax1/LLAMAX7 && ./start.sh --fast`

**Step 3: Manual verification checklist**

1. [ ] ImagesPage loads without errors
2. [ ] Existing batch images visible as folders under /Images/
3. [ ] Folder icons show with Lucide folder icon and correct colors
4. [ ] Double-click folder opens window with thumbnail grid
5. [ ] Right-click desktop background shows: New Folder, Select All, Sort By, Arrange
6. [ ] Right-click folder shows: Cut, Copy, Paste, Color, Rename, Delete
7. [ ] Right-click image shows: View Full Size, Cut, Copy, Download, Rename, Delete
8. [ ] New Folder creates a folder on desktop
9. [ ] Cut images from one folder, paste into another works
10. [ ] Image Gen tab still works and generates batches
11. [ ] Newly generated batch appears in Image Library tab after completion
12. [ ] Lightbox opens on double-click, arrow keys navigate, Escape closes
13. [ ] Window minimize/maximize/close all work
14. [ ] Window state persists across page navigation

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete ImagesPage overhaul with Documents integration"
```

---

## Task Summary

| Task | Description | Estimated Complexity |
|------|-------------|---------------------|
| 1 | Backend - Image Registration Service | Medium |
| 2 | Backend - Thumbnail Serving Endpoint | Small |
| 3 | Backend - Hook Batch Completion | Small |
| 4 | Backend - Migration Script | Medium |
| 5 | Frontend - ImagesContextMenu | Medium |
| 6 | Frontend - ImageThumbnailGrid | Medium |
| 7 | Frontend - ImageLightbox | Small |
| 8 | Frontend - Rewrite ImagesPage | Large |
| 9 | Frontend - Integration Polish | Medium |
| 10 | End-to-End Verification | Small |

**Dependencies:**
- Tasks 1-4 (backend) can be done in parallel with Tasks 5-7 (frontend components)
- Task 8 depends on Tasks 1, 2, 5, 6, 7
- Task 9 depends on Task 8
- Task 10 depends on all previous tasks
