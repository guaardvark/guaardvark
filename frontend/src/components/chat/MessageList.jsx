// frontend/src/components/chat/MessageList.jsx
// Version 1.0: Renders a list of messages and handles auto-scrolling.
import React, { useRef, useEffect, useImperativeHandle, forwardRef } from "react";
import { Box } from "@mui/material";
import MessageItem from "./MessageItem";

const MessageList = forwardRef(({ messages }, ref) => {
  const scrollRef = useRef(null);
  const safeMessages = Array.isArray(messages) ? messages : [];

  const scrollToBottom = () => {
    if (scrollRef.current) {
      requestAnimationFrame(() => {
        if (scrollRef.current) {
          scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
      });
    }
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Expose scrollToBottom function to parent components
  useImperativeHandle(ref, () => ({
    scrollToBottom
  }), []);

  return (
    <Box
      ref={scrollRef}
      sx={{
        flex: 1,
        overflowY: "auto",
        overflowX: "hidden",
        p: 2,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        minHeight: 0, // Allow flex item to shrink below content size
        maxHeight: '100%', // Ensure it doesn't exceed container
        scrollBehavior: 'smooth', // Smooth scrolling
        '&::-webkit-scrollbar': {
          width: '6px',
        },
        '&::-webkit-scrollbar-track': {
          backgroundColor: 'transparent',
        },
        '&::-webkit-scrollbar-thumb': {
          backgroundColor: 'rgba(0,0,0,0.2)',
          borderRadius: '3px',
        },
        '&::-webkit-scrollbar-thumb:hover': {
          backgroundColor: 'rgba(0,0,0,0.3)',
        },
      }}
    >
      {safeMessages.map((msg, index) => (
        <MessageItem key={msg.id || `msg-${index}`} message={msg} />
      ))}
    </Box>
  );
});

MessageList.displayName = "MessageList";

export default MessageList;
