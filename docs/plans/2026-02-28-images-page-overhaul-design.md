# ImagesPage Overhaul - Design Document

**Date:** 2026-02-28
**Status:** Approved

## Summary

Rebuild the ImagesPage to match DocumentsPage quality: thumbnail previews, right-click context menus, folder organization, drag-and-drop, and individual image management (cut/copy/paste/delete). Unify image storage by registering batch-generated images into the existing Documents/Files system.

## Key Decisions

1. **Register batch outputs into Documents system** - Batch generation stays untouched. On completion, images are registered as `Document` records under `/Images/<batch-name>/` folders. Reuses all existing files API infrastructure.
2. **Separate ImagesPage** - Remains its own page, scoped to `/Images/` root. Rebuilt to use files API and DocumentsPage patterns. Image Gen tab stays alongside.
3. **Auto-folder under /Images/** - Completed batches auto-create `/Images/<batch-name>/` folder. Users reorganize from there.
4. **Desktop icons** - Folders use Lucide `<Folder>` with custom colors. Individual images show thumbnail previews with folder-style frame.
5. **Organizational context menu** - Right-click desktop: New Folder, Select All, Sort By, Arrange. No upload (images come from generation).

## Section 1: Data Model & Backend

### Batch Completion Hook

When `BatchImageGenerator` finishes a batch, a new `register_batch_images()` function:

1. Creates `/Images/` root `Folder` if it doesn't exist
2. Creates `/Images/<batch-name>/` `Folder` record
3. Copies each generated image from `data/outputs/batch_images/<batch-id>/images/` to `data/uploads/Images/<batch-name>/`
4. Copies thumbnails from `data/outputs/batch_images/<batch-id>/thumbnails/` alongside
5. Creates a `Document` record per image with `type='image'`, linked to the folder

### What Stays the Same

- `BatchImageGenerator` and all generation endpoints
- Celery task pipeline
- All existing Documents/Files API endpoints
- No schema migrations needed - existing `Folder` and `Document` models suffice

### ImagesPage API

Reuses existing `/api/files/*` endpoints:
- `GET /api/files/browse?path=/Images/` - List image folders
- `POST /api/files/folder` - Create new folder under `/Images/`
- `POST /api/files/move` - Move images between folders
- `DELETE /api/files/delete-file` - Delete individual images
- All other CRUD via existing files API

## Section 2: Frontend - ImagesPage Rebuild

### Desktop Icons

- **Folders**: Lucide `<Folder>` icons with custom colors (8 color choices), same as DocumentsPage
- **Images at root level**: Thumbnail preview with folder-style frame border
- **Double-click folder**: Opens as draggable/resizable window
- **Drag folders**: Reposition on desktop

### Folder Windows

- Reuse `FolderWindowWrapper` for window chrome (title bar, color picker, minimize, close)
- Content renders as **thumbnail grid** by default (image-optimized)
- View mode toggle: grid (thumbnails) / list (file details)
- Breadcrumb navigation for subfolder browsing
- Lazy-loaded thumbnails with intersection observer

### Right-Click Context Menus

**Desktop background:**
- New Folder
- Select All
- Sort By (Name, Date, Size)
- Arrange Icons
- Arrange Windows
- Paste (if clipboard has content)

**Folder icon/window:**
- Cut / Copy
- Paste (if clipboard)
- Color Picker (8 colors)
- Rename
- Delete

**Individual image:**
- Cut / Copy
- Paste (if clipboard)
- View Full Size (lightbox)
- Download
- Rename
- Delete

### Drag-and-Drop

- Drag images between folder windows
- Drag images to desktop (moves to `/Images/` root)
- Drag folders to reposition on desktop
- Multi-select: Click + Shift (range) / Ctrl (toggle), drag-to-select box

### Cut/Copy/Paste

- Uses existing files API move/copy endpoints
- Works across folders
- Clipboard state: `{ items: [], operation: 'copy' | 'cut' }`

### Image Gen Tab

- Stays as-is
- On batch completion: toast notification that images are available in Image Library
- Batch completion triggers `register_batch_images()` which populates the files system

### Reused from DocumentsPage

- `FolderWindowWrapper` (window chrome)
- `DocumentsContextMenu` (adapted for image actions)
- `fileUtils.jsx` utilities
- Window state persistence pattern
- react-grid-layout configuration (48 cols, same constants)
- Icon dragging, z-index management

### New/Different from DocumentsPage

- Thumbnail grid as default view in folder contents
- Image lightbox viewer (from current `ImageBatchContents`)
- Thumbnail-with-frame desktop icons for root images
- No file upload action (images come from generation only)

## Section 3: Migration & Backward Compatibility

### One-Time Migration Script

- Scans `data/outputs/batch_images/` for completed batches
- For each batch: creates `/Images/<batch-name>/` folder, `Document` records, copies images to `data/uploads/Images/`
- Idempotent: checks if folder already exists before creating
- Can run manually or auto-trigger on first startup after upgrade

### Thumbnails

- Existing batch thumbnails (256x256 JPEG) copied alongside full images
- Frontend uses thumbnails for grid/icon display
- Full resolution loaded only for lightbox view

### Old Batch API

- All endpoints remain functional
- Generation still writes to `data/outputs/batch_images/`
- Registration hook copies to documents system post-completion
- No breaking changes

## Section 4: Explicit Non-Goals

- **RAG indexing of images** - Not auto-indexed. Can be added later.
- **DocumentsPage changes** - No modifications. Images visible under `/Images/` but no special handling.
- **Batch generation changes** - Pipeline, Celery, ComfyUI untouched.
- **Cross-tab sync** - Not critical for v1.
- **Image editing/cropping** - Out of scope.
- **Batch-to-project linking** - Not added now. Users organize via folders.
