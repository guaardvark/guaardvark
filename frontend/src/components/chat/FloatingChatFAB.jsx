import React from "react";
import { Box, ButtonBase, Fab, Badge, Tooltip, Zoom } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { BrandLogo } from "../branding";
import { useAppStore } from "../../stores/useAppStore";
import { useFloatingChatStore } from "../../stores/useFloatingChatStore";
import { useUiTranslation } from "../../i18n";

const FloatingChatFAB = () => {
  const { t } = useUiTranslation();
  const isOpen = useFloatingChatStore((s) => s.isOpen);
  const toggleOpen = useFloatingChatStore((s) => s.toggleOpen);
  const hasMessages = useFloatingChatStore((s) => s.messages.length > 0);
  const systemLogo = useAppStore((s) => s.systemLogo);
  const hideFab = useFloatingChatStore((s) => s.hideFab);
  const hidden = useFloatingChatStore(
    (s) => s.fabHiddenThisSession || (s.fabHidden && s.barChatEntry)
  );

  return (
    <Zoom in={!isOpen && !hidden} unmountOnExit>
      <Box
        sx={{
          position: "fixed",
          bottom: 40,
          right: 24,
          zIndex: 1400,
          "& .floating-chat-dismiss": { opacity: 0 },
          "&:hover .floating-chat-dismiss, & .floating-chat-dismiss:focus-visible": { opacity: 1 },
        }}
      >
        <Tooltip title={t("chat.floating.openShortcut")} placement="left">
          <Fab
            color="primary"
            onClick={toggleOpen}
            size="medium"
            sx={{
              bgcolor: "#000",
              color: "#fff",
              border: "1px solid rgba(255, 255, 255, 0.24)",
              boxShadow: "0 10px 24px rgba(0, 0, 0, 0.35)",
              "&:hover": {
                bgcolor: "#111",
                borderColor: "rgba(255, 255, 255, 0.6)",
              },
              "&:focus-visible": {
                boxShadow: "0 0 0 3px rgba(255, 255, 255, 0.18)",
              },
            }}
          >
            <Badge variant="dot" color="error" invisible={!hasMessages}>
              <Box
                component="span"
                sx={{
                  width: 32,
                  height: 32,
                  borderRadius: "50%",
                  bgcolor: "#000",
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  overflow: "hidden",
                }}
              >
                {systemLogo ? (
                  <Box
                    component="img"
                    src={`/api/uploads/${systemLogo}`}
                    alt="Guaardvark"
                    sx={{
                      width: 24,
                      height: 24,
                      objectFit: "contain",
                    }}
                  />
                ) : (
                  <BrandLogo size={24} color="#fff" />
                )}
              </Box>
            </Badge>
          </Fab>
        </Tooltip>
        <ButtonBase
          className="floating-chat-dismiss"
          onClick={hideFab}
          aria-label={t("chat.floating.hideButton")}
          title={t("chat.floating.hideTooltip")}
          sx={{
            position: "absolute",
            top: -6,
            right: -6,
            width: 16,
            height: 16,
            borderRadius: "50%",
            bgcolor: "#000",
            color: "#fff",
            border: "1px solid rgba(255, 255, 255, 0.4)",
            transition: "opacity 120ms",
            "& .MuiSvgIcon-root": { fontSize: 11 },
          }}
        >
          <CloseIcon />
        </ButtonBase>
      </Box>
    </Zoom>
  );
};

export default FloatingChatFAB;
