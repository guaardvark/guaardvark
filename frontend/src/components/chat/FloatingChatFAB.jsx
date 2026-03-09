import React from "react";
import { Fab, Badge, Tooltip, Zoom } from "@mui/material";
import { GuaardvarkLogo } from "../branding";
import { useFloatingChatStore } from "../../stores/useFloatingChatStore";

const FloatingChatFAB = () => {
  const isOpen = useFloatingChatStore((s) => s.isOpen);
  const toggleOpen = useFloatingChatStore((s) => s.toggleOpen);
  const hasMessages = useFloatingChatStore((s) => s.messages.length > 0);

  return (
    <Zoom in={!isOpen} unmountOnExit>
      <Tooltip title="Open chat (Ctrl+Shift+C)" placement="left">
        <Fab
          color="primary"
          onClick={toggleOpen}
          size="medium"
          sx={{
            position: "fixed",
            bottom: 40,
            right: 24,
            zIndex: 1400,
          }}
        >
          <Badge variant="dot" color="error" invisible={!hasMessages}>
            <GuaardvarkLogo size={24} />
          </Badge>
        </Fab>
      </Tooltip>
    </Zoom>
  );
};

export default FloatingChatFAB;
