// Where files and folders come in from this computer: a drop target with
// Choose folder… and Choose files… for the same thing by click. Once a source
// is chosen the zone shrinks to its children (the one-line summary) and stays
// a drop target, so a new drop replaces the source.
import React, { useRef, useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { ActionButton } from "../../settings/ui";
import { dragCarriesFiles, snapshotDrop } from "../../../utils/droppedFiles";
import { useUiTranslation } from "../../../i18n";

// webkitdirectory is not in every browser; without it the folder button is hidden.
const supportsFolderPicker = () =>
  typeof document !== "undefined" && "webkitdirectory" in document.createElement("input");

const ImportDropZone = ({ onDrop, onPick, disabled = false, disabledReason = "", compact = false, children }) => {
  const { t } = useUiTranslation();
  const [over, setOver] = useState(false);
  const folderInput = useRef(null);
  const filesInput = useRef(null);
  const folderPicker = supportsFolderPicker();

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setOver(false);
    if (disabled || !dragCarriesFiles(e)) return;
    // Before any await: the browser empties the item list once this handler yields.
    onDrop(snapshotDrop(e.dataTransfer));
  };

  const handlePicked = (e) => {
    const files = e.target.files;
    if (files && files.length) onPick(files);
    e.target.value = "";
  };

  return (
    <Box
      data-testid="import-drop-zone"
      onDragOver={(e) => {
        if (!dragCarriesFiles(e)) return;
        e.preventDefault();
        e.stopPropagation();
        e.dataTransfer.dropEffect = disabled ? "none" : "copy";
        if (!disabled) setOver(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) setOver(false);
      }}
      onDrop={handleDrop}
      sx={(theme) => ({
        border: "1px dashed",
        borderColor: over ? theme.palette.primary.main : theme.palette.divider,
        bgcolor: over ? alpha(theme.palette.primary.main, 0.08) : "transparent",
        borderRadius: "8px",
        transition: "background-color 120ms, border-color 120ms",
        px: 2,
        py: compact ? 1 : { xs: 2, sm: 3 },
      })}
    >
      <input ref={folderInput} type="file" webkitdirectory="" multiple hidden onChange={handlePicked} />
      <input ref={filesInput} type="file" multiple hidden onChange={handlePicked} />
      {compact ? (
        children
      ) : (
        <Stack spacing={1} alignItems="center" textAlign="center">
          <Typography variant="body1">
            {over ? t("upload.bulk.dropZone.release") : disabled && disabledReason ? disabledReason : t("upload.bulk.dropZone.prompt")}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {t("upload.bulk.dropZone.note")}
          </Typography>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap justifyContent="center">
            {folderPicker && (
              <ActionButton disabled={disabled} onClick={() => folderInput.current?.click()}>
                {t("upload.bulk.dropZone.chooseFolder")}
              </ActionButton>
            )}
            <ActionButton disabled={disabled} onClick={() => filesInput.current?.click()}>
              {t("upload.bulk.dropZone.chooseFiles")}
            </ActionButton>
          </Stack>
        </Stack>
      )}
    </Box>
  );
};

export default ImportDropZone;
