// frontend/src/components/codeeditor/AICommandPalette.jsx
// Enhanced command palette for AI assistant with search and keyboard navigation

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Box,
  Paper,
  TextField,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  ListItemSecondaryAction,
  Typography,
  Chip,
  InputAdornment,
  Divider,
  Fade,
} from '@mui/material';
import {
  Search as SearchIcon,
  KeyboardArrowRight as ArrowIcon,
} from '@mui/icons-material';

const AICommandPalette = ({
  commands = {},
  isOpen = false,
  onClose,
  onCommandSelect,
  currentContext = null,
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);

  // Filter and sort commands based on search term and context
  const filteredCommands = useMemo(() => {
    const commandEntries = Object.entries(commands);

    if (!searchTerm) {
      return commandEntries;
    }

    return commandEntries.filter(([key, command]) => {
      const searchLower = searchTerm.toLowerCase();
      return (
        key.toLowerCase().includes(searchLower) ||
        command.label.toLowerCase().includes(searchLower) ||
        command.description.toLowerCase().includes(searchLower)
      );
    });
  }, [commands, searchTerm]);

  // Reset selection when filtered commands change
  useEffect(() => {
    setSelectedIndex(0);
  }, [filteredCommands]);

  // Handle keyboard navigation
  const handleKeyDown = useCallback((event) => {
    if (!isOpen) return;

    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        setSelectedIndex(prev =>
          prev < filteredCommands.length - 1 ? prev + 1 : 0
        );
        break;
      case 'ArrowUp':
        event.preventDefault();
        setSelectedIndex(prev =>
          prev > 0 ? prev - 1 : filteredCommands.length - 1
        );
        break;
      case 'Enter':
        event.preventDefault();
        if (filteredCommands[selectedIndex]) {
          const [commandKey] = filteredCommands[selectedIndex];
          onCommandSelect(commandKey);
          onClose();
        }
        break;
      case 'Escape':
        event.preventDefault();
        onClose();
        break;
      default:
        break;
    }
  }, [isOpen, filteredCommands, selectedIndex, onCommandSelect, onClose]);

  // Add keyboard event listener
  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
      return () => document.removeEventListener('keydown', handleKeyDown);
    }
  }, [isOpen, handleKeyDown]);

  // Clear search when opening
  useEffect(() => {
    if (isOpen) {
      setSearchTerm('');
      setSelectedIndex(0);
    }
  }, [isOpen]);

  const handleCommandClick = useCallback((commandKey) => {
    onCommandSelect(commandKey);
    onClose();
  }, [onCommandSelect, onClose]);

  if (!isOpen) return null;

  return (
    <Fade in={isOpen} timeout={200}>
      <Box
        sx={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          bgcolor: 'rgba(0, 0, 0, 0.5)',
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'center',
          pt: '20vh',
          zIndex: 1300,
        }}
        onClick={onClose}
      >
        <Paper
          elevation={8}
          onClick={(e) => e.stopPropagation()}
          sx={{
            width: '90%',
            maxWidth: '600px',
            maxHeight: '60vh',
            display: 'flex',
            flexDirection: 'column',
            borderRadius: 2,
            overflow: 'hidden',
          }}
        >
          {/* Header */}
          <Box sx={{ p: 2, bgcolor: 'primary.main', color: 'primary.contrastText' }}>
            <Typography variant="h6" sx={{ mb: 1 }}>
              AI Command Palette
            </Typography>
            <TextField
              fullWidth
              size="small"
              placeholder="Search commands..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon sx={{ color: 'text.secondary' }} />
                  </InputAdornment>
                ),
              }}
              sx={{
                '& .MuiOutlinedInput-root': {
                  bgcolor: 'background.paper',
                  '& fieldset': {
                    borderColor: 'divider',
                  },
                },
              }}
              autoFocus
            />
          </Box>

          {/* Context Info */}
          {currentContext && (
            <Box sx={{ px: 2, py: 1, bgcolor: 'action.hover' }}>
              <Typography variant="caption" color="text.secondary">
                Current file: {currentContext.filePath || 'untitled'} ({currentContext.language || 'text'})
              </Typography>
            </Box>
          )}

          {/* Commands List */}
          <Box sx={{ flex: 1, overflow: 'auto' }}>
            {filteredCommands.length === 0 ? (
              <Box sx={{ p: 3, textAlign: 'center' }}>
                <Typography color="text.secondary">
                  No commands found matching "{searchTerm}"
                </Typography>
              </Box>
            ) : (
              <List sx={{ py: 0 }}>
                {filteredCommands.map(([commandKey, command], index) => {
                  const IconComponent = command.icon;
                  const isSelected = index === selectedIndex;

                  return (
                    <React.Fragment key={commandKey}>
                      <ListItem
                        button
                        selected={isSelected}
                        onClick={() => handleCommandClick(commandKey)}
                        sx={{
                          py: 1.5,
                          px: 2,
                          cursor: 'pointer',
                          transition: 'all 0.2s',
                          '&:hover': {
                            bgcolor: 'action.hover',
                          },
                          '&.Mui-selected': {
                            bgcolor: 'primary.light',
                            color: 'primary.contrastText',
                            '&:hover': {
                              bgcolor: 'primary.main',
                            },
                          },
                        }}
                      >
                        <ListItemIcon
                          sx={{
                            color: isSelected ? 'inherit' : command.color + '.main',
                            minWidth: '40px',
                          }}
                        >
                          <IconComponent />
                        </ListItemIcon>
                        <ListItemText
                          primary={
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                              <Typography variant="body1" sx={{ fontWeight: 'medium' }}>
                                /{commandKey}
                              </Typography>
                              <Chip
                                label={command.label}
                                size="small"
                                variant="outlined"
                                sx={{
                                  height: '20px',
                                  fontSize: '0.7rem',
                                  borderColor: isSelected ? 'currentColor' : command.color + '.main',
                                  color: isSelected ? 'inherit' : command.color + '.main',
                                }}
                              />
                            </Box>
                          }
                          secondary={
                            <Typography
                              variant="body2"
                              sx={{
                                color: isSelected ? 'inherit' : 'text.secondary',
                                opacity: isSelected ? 0.9 : 1,
                              }}
                            >
                              {command.description}
                            </Typography>
                          }
                        />
                        <ListItemSecondaryAction>
                          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <Chip
                              label={command.shortcut}
                              size="small"
                              variant="outlined"
                              sx={{
                                height: '20px',
                                fontSize: '0.6rem',
                                opacity: isSelected ? 0.9 : 0.7,
                                borderColor: isSelected ? 'currentColor' : 'text.secondary',
                                color: isSelected ? 'inherit' : 'text.secondary',
                              }}
                            />
                            {isSelected && (
                              <ArrowIcon
                                sx={{
                                  color: 'inherit',
                                  fontSize: '1.2rem',
                                }}
                              />
                            )}
                          </Box>
                        </ListItemSecondaryAction>
                      </ListItem>
                      {index < filteredCommands.length - 1 && (
                        <Divider variant="inset" component="li" />
                      )}
                    </React.Fragment>
                  );
                })}
              </List>
            )}
          </Box>

          {/* Footer */}
          <Box sx={{ px: 2, py: 1, bgcolor: 'action.hover', borderTop: 1, borderColor: 'divider' }}>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
              Use ↑↓ to navigate, Enter to select, Esc to close
            </Typography>
          </Box>
        </Paper>
      </Box>
    </Fade>
  );
};

export default AICommandPalette;