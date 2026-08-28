import React, { useEffect } from "react";
import { useLocation } from "react-router-dom";
import FloatingChatCard from "./FloatingChatCard";
import FloatingChatFAB from "./FloatingChatFAB";
import { useFloatingChatStore } from "../../stores/useFloatingChatStore";
import { usePageContext } from "../../hooks/usePageContext";
import { isFloatingChatHiddenRoute } from "../../config/floatingChat";

const FloatingChatProvider = () => {
  const location = useLocation();
  const pageContext = usePageContext();
  const isOpen = useFloatingChatStore((s) => s.isOpen);
  const toggleOpen = useFloatingChatStore((s) => s.toggleOpen);
  const setIsOpen = useFloatingChatStore((s) => s.setIsOpen);
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

  // A chat surface of its own does not get a second chat floating over it.
  // Close the card too, so it does not pop back open on the next page.
  const hidden = isFloatingChatHiddenRoute(location.pathname);
  useEffect(() => {
    if (hidden && isOpen) {
      setIsOpen(false);
    }
  }, [hidden, isOpen, setIsOpen]);

  if (hidden) return null;

  return (
    <>
      <FloatingChatFAB />
      <FloatingChatCard />
    </>
  );
};

export default FloatingChatProvider;
