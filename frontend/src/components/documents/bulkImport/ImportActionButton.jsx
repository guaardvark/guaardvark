// One Import button with the other ways to run it in its menu: Import (a copy;
// the source is left as it is), Preview only, and Import and move…, which is
// offered for a folder on the Guaardvark machine only (a browser cannot remove
// the files it uploaded) and always asks first.
import React, { useRef, useState } from "react";
import { Button, ButtonGroup, ListItemText, Menu, MenuItem, Tooltip } from "@mui/material";
import ArrowDropDownIcon from "@mui/icons-material/ArrowDropDown";
import { translate, useUiTranslation } from "../../../i18n";

const ImportActionButton = ({
  label = translate("upload.bulk.action.import"),
  disabled = false,
  disabledReason = "",
  canPreview = false,
  canMove = false,
  onImport,
  onPreview,
  onMove,
}) => {
  const { t } = useUiTranslation();
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);
  const run = (handler) => () => {
    setOpen(false);
    handler?.();
  };

  const group = (
    <ButtonGroup variant="contained" size="small" ref={anchorRef} disabled={disabled} aria-label={t("upload.bulk.action.import")}>
      <Button onClick={onImport} sx={{ px: 2, whiteSpace: "nowrap" }}>
        {label}
      </Button>
      <Button
        size="small"
        aria-label={t("upload.bulk.action.moreWays")}
        aria-haspopup="menu"
        aria-expanded={open ? "true" : undefined}
        onClick={() => setOpen((v) => !v)}
        sx={{ px: 0.5, minWidth: 0 }}
      >
        <ArrowDropDownIcon fontSize="small" />
      </Button>
    </ButtonGroup>
  );

  return (
    <>
      {disabled && disabledReason ? (
        <Tooltip title={disabledReason}>
          <span style={{ display: "inline-flex" }}>{group}</span>
        </Tooltip>
      ) : (
        group
      )}
      <Menu
        anchorEl={anchorRef.current}
        open={open}
        onClose={() => setOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <MenuItem onClick={run(onImport)}>
          <ListItemText primary={t("upload.bulk.action.import")} secondary={t("upload.bulk.action.importHint")} />
        </MenuItem>
        <MenuItem onClick={run(onPreview)} disabled={!canPreview}>
          <ListItemText
            primary={t("upload.bulk.action.previewOnly")}
            secondary={canPreview ? t("upload.bulk.action.previewHint") : t("upload.bulk.action.previewCounted")}
          />
        </MenuItem>
        <MenuItem onClick={run(onMove)} disabled={!canMove}>
          <ListItemText
            primary={t("upload.bulk.action.importAndMove")}
            secondary={canMove
              ? t("upload.bulk.action.moveHint")
              : t("upload.bulk.action.moveServerOnly")}
          />
        </MenuItem>
      </Menu>
    </>
  );
};

export default ImportActionButton;
