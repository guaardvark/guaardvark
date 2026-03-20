import { useState, useCallback, useEffect, useRef } from "react";
import { getAllCommands, getBuiltInCommands, filterCommands, parseCommand } from "../utils/slashCommandRegistry";

/**
 * Shared slash command hook for all chat inputs.
 *
 * @param {Object} config
 * @param {React.RefObject} config.inputRef — TextField DOM ref for Popper anchoring
 * @param {Function} config.addMessage — add message to chat display: ({ role, content, type, tempId })
 * @param {Function} config.updateMessage — update message by tempId: (tempId, updates)
 * @param {Function} config.onSendMessage — existing send callback for normal chat flow
 * @param {Function} config.setInputText — set the input text (for command insertion)
 * @param {Object} config.chatState — { sessionId, projectId, onPlanCreated, voiceContext }
 */
export default function useSlashCommands({ inputRef, addMessage, updateMessage, onSendMessage, setInputText, chatState }) {
  const [allCommands, setAllCommands] = useState(getBuiltInCommands());
  const [filteredCommands, setFilteredCommands] = useState([]);
  const [popupVisible, setPopupVisible] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [isCommand, setIsCommand] = useState(false);
  const currentInput = useRef("");

  // Fetch DB commands on mount
  useEffect(() => {
    getAllCommands().then(setAllCommands);
  }, []);

  const handleInputChange = useCallback((text) => {
    currentInput.current = text;
    const trimmed = text.trim();

    if (trimmed.startsWith("/")) {
      setIsCommand(true);
      const matches = filterCommands(allCommands, trimmed);
      setFilteredCommands(matches);
      setPopupVisible(matches.length > 0);
      setSelectedIndex(0);
    } else {
      setIsCommand(false);
      setPopupVisible(false);
      setFilteredCommands([]);
    }
  }, [allCommands]);

  const handleKeyDown = useCallback((event) => {
    if (!popupVisible) return;

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        event.stopPropagation();
        setSelectedIndex((prev) => Math.min(prev + 1, filteredCommands.length - 1));
        break;
      case "ArrowUp":
        event.preventDefault();
        event.stopPropagation();
        setSelectedIndex((prev) => Math.max(prev - 1, 0));
        break;
      case "Enter":
      case "Tab":
        event.preventDefault();
        event.stopPropagation();
        if (filteredCommands[selectedIndex]) {
          selectCommand(filteredCommands[selectedIndex]);
        }
        break;
      case "Escape":
        event.preventDefault();
        event.stopPropagation();
        setPopupVisible(false);
        break;
      default:
        break;
    }
  }, [popupVisible, filteredCommands, selectedIndex]);

  const selectCommand = useCallback((cmd) => {
    setPopupVisible(false);
    if (cmd.args === "none") {
      // Execute immediately
      executeCommand(cmd.name);
      if (setInputText) setInputText("");
    } else {
      // Insert command + space, user types args
      if (setInputText) setInputText(cmd.name + " ");
    }
  }, [setInputText]);

  const executeCommand = useCallback(async (text) => {
    const { name, args } = parseCommand(text);

    // Find the command in registry
    const cmd = allCommands.find((c) => c.name === name);
    if (!cmd) return { handled: false };

    // Route to handler — each handler is imported from the execution module
    try {
      const { executeBuiltinCommand } = await import("./slashCommandHandlers");
      return await executeBuiltinCommand(name, args, {
        addMessage, updateMessage, onSendMessage, chatState, allCommands,
      });
    } catch (err) {
      console.error("Command execution error:", err);
      addMessage?.({
        role: "system",
        content: `Command failed: ${err.message}`,
        tempId: `err-${Date.now()}`,
      });
      return { handled: true };
    }
  }, [allCommands, addMessage, updateMessage, onSendMessage, chatState]);

  return {
    popupVisible,
    filteredCommands,
    selectedIndex,
    handleInputChange,
    handleKeyDown,
    selectCommand,
    executeCommand,
    isCommand,
  };
}
