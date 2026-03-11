import React from 'react';
import { Box, Typography } from '@mui/material';

/**
 * Standardized empty state display for lists, tables, and cards.
 * @param {object} props
 * @param {React.ReactNode} props.icon - MUI icon element (e.g., <InboxOutlined />)
 * @param {string} props.title - Main message (e.g., "No projects found")
 * @param {string} [props.description] - Optional secondary text
 * @param {React.ReactNode} [props.action] - Optional action button
 */
export default function EmptyState({ icon, title, description, action }) {
  return (
    <Box sx={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      py: 6,
      px: 2,
      opacity: 0.7,
    }}>
      {icon && (
        <Box sx={{ mb: 1.5, color: 'text.secondary', '& .MuiSvgIcon-root': { fontSize: 48 } }}>
          {icon}
        </Box>
      )}
      <Typography variant="body1" color="text.secondary" sx={{ mb: description ? 0.5 : 0 }}>
        {title}
      </Typography>
      {description && (
        <Typography variant="body2" color="text.disabled">
          {description}
        </Typography>
      )}
      {action && <Box sx={{ mt: 2 }}>{action}</Box>}
    </Box>
  );
}
