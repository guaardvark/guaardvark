import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import ManageBackupsModal, { formatBytes } from "../ManageBackupsModal";

const backups = [
  { name: "auto_daily_20261014_154823.zip", size: 1754523616, type: "auto", modified: 1760000000, pinned: true },
  { name: "auto_daily_20261025_230540.zip", size: 2503363988, type: "auto", modified: 1761000000, pinned: false },
];

describe("ManageBackupsModal pins", () => {
  it("shows a pinned chip, blocks delete on a pinned backup and reports the toggle", () => {
    const onPin = vi.fn();
    const onDelete = vi.fn();
    render(<ManageBackupsModal open onClose={() => {}} backups={backups} onPin={onPin} onDelete={onDelete} />);

    expect(screen.getAllByText("Pinned").length).toBe(1);

    const pinButtons = screen.getAllByRole("button", { pressed: true });
    expect(pinButtons.length).toBe(1);
    fireEvent.click(pinButtons[0]);
    expect(onPin).toHaveBeenCalledWith("auto_daily_20261014_154823.zip", false);

    const unpinned = screen.getAllByRole("button", { pressed: false });
    fireEvent.click(unpinned[0]);
    expect(onPin).toHaveBeenCalledWith("auto_daily_20261025_230540.zip", true);

    const deleteButtons = screen.getAllByTestId("DeleteIcon").map((i) => i.closest("button"));
    expect(deleteButtons[0]).toBeDisabled();
    expect(deleteButtons[1]).not.toBeDisabled();
  });

  it("saves the automatic-backup limits only once something changed", async () => {
    const onSaveSettings = vi.fn().mockResolvedValue({});
    const settings = {
      backup_daily_enabled: "true",
      backup_keep_last: "7",
      backup_max_age_days: "30",
      backup_max_total_gb: "0",
      backup_min_free_gb: "5",
      disk: { ok: true, free: 200 * 1024 ** 3, needed: 8 * 1024 ** 3 },
    };
    render(<ManageBackupsModal open onClose={() => {}} backups={[]} settings={settings} onSaveSettings={onSaveSettings} />);
    const save = screen.getByRole("button", { name: "Save limits" });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Always keep the newest"), { target: { value: "10" } });
    expect(save).not.toBeDisabled();
    fireEvent.click(save);
    expect(onSaveSettings).toHaveBeenCalledWith(expect.objectContaining({ backup_keep_last: "10", backup_daily_enabled: true }));
  });

  it("formats sizes up to terabytes", () => {
    expect(formatBytes(2503363988)).toBe("2.3 GB");
    expect(formatBytes(3 * 1024 ** 4)).toBe("3 TB");
  });
});
