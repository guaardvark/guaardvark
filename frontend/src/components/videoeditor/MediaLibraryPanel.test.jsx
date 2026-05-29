import React from 'react';
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import MediaLibraryPanel from "./MediaLibraryPanel";

const itemInFolder = (id, folderId) => ({
  id, filename: `clip_${id}.mp4`, folder_id: folderId,
  folder: { id: folderId, name: "DoomBatch", path: "Videos/DoomBatch" },
  thumbnail_url: null, metadata: {}, size: 1, uploaded_at: "2026-05-08T00:00:00Z",
});

describe("MediaLibraryPanel", () => {
  it("drilling into a folder shows the items, not the folder again", () => {
    const videos = [itemInFolder(1, 7), itemInFolder(2, 7)];
    render(
      <MediaLibraryPanel
        videos={videos}
        audios={[]}
        images={[]}
        tabIndex={0}
        onItemDragStart={vi.fn()}
      />
    );

    // Initial view: should see the folder tile
    const folderTile = screen.getByText("DoomBatch");
    expect(folderTile).toBeInTheDocument();

    // Click the folder to drill in
    fireEvent.click(folderTile);

    // Now we should see the items
    expect(screen.getByText("clip_1.mp4")).toBeInTheDocument();
    expect(screen.getByText("clip_2.mp4")).toBeInTheDocument();

    // The folder tile should NOT be present (only the breadcrumb might be, but it's a link/text, not a folder tile)
    // In grid view, folder tiles are rendered with the folder icon or specific role.
    // Let's check that there's no element with the exact folder tile text if possible,
    // or just that the items are visible. Wait, the breadcrumb has "DoomBatch".
    // Let's ensure there's only one "DoomBatch" text (the breadcrumb) and not two.
    const doomBatchTexts = screen.getAllByText("DoomBatch");
    expect(doomBatchTexts.length).toBe(1); // Only the breadcrumb
  });

  it("drilling out via breadcrumb returns to the folder view", () => {
    const videos = [itemInFolder(1, 7), itemInFolder(2, 7)];
    render(
      <MediaLibraryPanel
        videos={videos}
        audios={[]}
        images={[]}
        tabIndex={0}
        onItemDragStart={vi.fn()}
      />
    );

    // Drill in
    fireEvent.click(screen.getByText("DoomBatch"));
    
    // Verify items are visible
    expect(screen.getByText("clip_1.mp4")).toBeInTheDocument();

    // Click breadcrumb back
    const backLink = screen.getByText("Library");
    fireEvent.click(backLink);

    // Items should be hidden
    expect(screen.queryByText("clip_1.mp4")).not.toBeInTheDocument();

    // Folder tile should be back
    expect(screen.getByText("DoomBatch")).toBeInTheDocument();
  });
});
