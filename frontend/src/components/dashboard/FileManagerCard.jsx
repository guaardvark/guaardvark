// frontend/src/components/dashboard/FileManagerCard.jsx
// Window-based FileManager card for DashboardPage
// Wraps FileManager component with DashboardCardWrapper for window-based UI

import React from "react";
import DashboardCardWrapper from "./DashboardCardWrapper";
import FileManager from "../filesystem/FileManager";
import { useUiTranslation } from "../../i18n";

const FileManagerCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      ...props
    },
    ref,
  ) => {
    const { t } = useUiTranslation();
    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title={t("dashboard.files.title")}
        {...props}
        contextMenuArea="header"
      >
        <FileManager />
      </DashboardCardWrapper>
    );
  }
);

FileManagerCard.displayName = "FileManagerCard";

export default FileManagerCard;

