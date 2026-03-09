// frontend/src/components/dashboard/FileManagerCard.jsx
// Window-based FileManager card for DashboardPage
// Wraps FileManager component with DashboardCardWrapper for window-based UI

import React from "react";
import DashboardCardWrapper from "./DashboardCardWrapper";
import FileManager from "../filesystem/FileManager";

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
    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title="File Manager"
        {...props}
      >
        <FileManager />
      </DashboardCardWrapper>
    );
  }
);

FileManagerCard.displayName = "FileManagerCard";

export default FileManagerCard;

