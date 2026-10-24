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
import { useUiTranslation, translate, uiLocale } from '../../i18n';

const getStatusColor = (status, stage) => {
  if (status === 'complete' || stage === 'complete') return 'success';
  if (status?.startsWith('failed')) return 'error';
  if (['casting', 'awaiting_approval'].includes(stage) || ['casting', 'awaiting_approval'].includes(status)) {
    return 'warning';
  }
  return 'info';
};

const chipLabel = (p) => {
  if (p.status?.startsWith('failed')) {
    return translate('filmCrew.list.failedAt', { stage: (p.current_stage || '').replace(/_/g, ' ') });
  }
  return (p.current_stage || p.status || '').replace(/_/g, ' ');
};

const ProductionList = ({ productions, selectedId, onSelect }) => {
  const { t } = useUiTranslation();
  return (
    <Paper sx={{ height: '100%', overflowY: 'auto', borderRight: 1, borderColor: 'divider' }}>
      <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Typography variant="h6">{t('filmCrew.list.title')}</Typography>
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
                secondaryTypographyProps={{ component: 'div' }}
                secondary={
                  <Box component="div" sx={{ display: 'flex', alignItems: 'center', mt: 0.5 }}>
                    <Chip
                      label={chipLabel(p)}
                      size="small"
                      color={getStatusColor(p.status, p.current_stage)}
                      sx={{ height: 20, fontSize: '0.65rem' }}
                    />
                    <Typography component="span" variant="caption" sx={{ ml: 1 }}>
                      {new Date(p.created_at).toLocaleDateString(uiLocale())}
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
              {t('filmCrew.list.empty')}
            </Typography>
          </Box>
        )}
      </List>
    </Paper>
  );
};

export default ProductionList;
