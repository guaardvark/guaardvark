// Header button for a page that remembers its settings: puts them back to the
// defaults. Shows nothing on a page without a header.

import React from "react";
import { createPortal } from "react-dom";
import PropTypes from "prop-types";
import { IconButton, Tooltip } from "@mui/material";
import SettingsBackupRestoreIcon from "@mui/icons-material/SettingsBackupRestore";
import { usePageHeaderSlot } from "./pageHeaderSlot";
import { useUiTranslation } from "../../i18n";

const ResetSettingsButton = ({ onReset, disabled = false }) => {
  const { t } = useUiTranslation();
  const slot = usePageHeaderSlot("actions");
  if (!slot) return null;
  return createPortal(
    <Tooltip title={t("layout.savedSettings.resetHint")}>
      <span>
        <IconButton
          size="small"
          onClick={onReset}
          disabled={disabled}
          aria-label={t("layout.savedSettings.reset")}
        >
          <SettingsBackupRestoreIcon fontSize="small" />
        </IconButton>
      </span>
    </Tooltip>,
    slot,
  );
};

ResetSettingsButton.propTypes = { onReset: PropTypes.func.isRequired, disabled: PropTypes.bool };

export default ResetSettingsButton;
