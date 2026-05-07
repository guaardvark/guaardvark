import React from 'react';
import { 
  List, 
  ListItem, 
  ListItemText, 
  ListItemButton, 
  Typography, 
  Paper, 
  Chip,
  Box
} from '@mui/material';

const getStatusColor = (status) => {
  if (status === 'complete') return 'success';
  if (status.startsWith('failed')) return 'error';
  if (['casting', 'awaiting_approval'].includes(status)) return 'warning';
  return 'info';
};

const ProductionList = ({ productions, selectedId, onSelect }) => {
  return (
    <Paper sx={{ height: '100%', overflowY: 'auto', borderRight: 1, borderColor: 'divider' }}>
      <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Typography variant="h6">Productions</Typography>
      </Box>
      <List>
        {productions.map((p) => (
          <ListItem key={p.id} disablePadding>
            <ListItemButton 
              selected={selectedId === p.id}
              onClick={() => onSelect(p.id)}
            >
              <ListItemText 
                primary={p.name}
                secondary={
                  <Box component="span" sx={{ display: 'flex', alignItems: 'center', mt: 0.5 }}>
                    <Chip 
                      label={p.current_stage?.replace('_', ' ') || p.status} 
                      size="small" 
                      color={getStatusColor(p.current_stage || p.status)}
                      sx={{ height: 20, fontSize: '0.65rem' }}
                    />
                    <Typography variant="caption" sx={{ ml: 1 }}>
                      {new Date(p.created_at).toLocaleDateString()}
                    </Typography>
                  </Box>
                }
              />
            </ListItemButton>
          </ListItem>
        ))}
        {productions.length === 0 && (
          <Box sx={{ p: 3, textAlign: 'center' }}>
            <Typography variant="body2" color="text.secondary">
              No productions yet. Create one to get started!
            </Typography>
          </Box>
        )}
      </List>
    </Paper>
  );
};

export default ProductionList;
