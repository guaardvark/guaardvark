import React, { useEffect } from "react";
import { useLocation } from "react-router-dom";
import FloatingChatCard from "./FloatingChatCard";
import FloatingChatFAB from "./FloatingChatFAB";
import { useFloatingChatStore } from "../../stores/useFloatingChatStore";
import { usePageContext } from "../../hooks/usePageContext";

const FloatingChatProvider = () => {
  const location = useLocation();
  const pageContext = usePageContext();
  const toggleOpen = useFloatingChatStore((s) => s.toggleOpen);
  const setPageContext = useFloatingChatStore((s) => s.setPageContext);

  // Sync page context to store on route change
  useEffect(() => {
    setPageContext(pageContext);
  }, [pageContext, setPageContext]);

  // Global keyboard shortcut: Ctrl+Shift+C
  useEffect(() => {
    const handler = (e) => {
      if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "c") {
        e.preventDefault();
        toggleOpen();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [toggleOpen]);

  // Hide on /chat page to avoid two chat UIs
  if (location.pathname === "/chat") return null;

  return (
    <>
      <FloatingChatFAB />
      <FloatingChatCard />
    </>
  );
};

export default FloatingChatProvider;
