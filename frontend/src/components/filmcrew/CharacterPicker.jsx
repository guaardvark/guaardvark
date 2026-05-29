import React, { useState, useEffect } from 'react';
import {
  Box,
  Card,
  Typography,
  Chip,
  CircularProgress,
  useTheme
} from '@mui/material';
import { listCastLibrary } from '../../api/productionService';

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

/**
 * CharacterPicker component for selecting trained subjects.
 * 
 * @param {number[]} value - Selected subject IDs (controlled)
 * @param {function} onChange - Callback for when selection changes (ids: number[]) => void
 * @param {boolean} multiple - If true, allow multiple selection (default: true)
 * @param {string} kind - Filter subjects by this kind (default: 'character')
 * @param {boolean} onlyTrained - If true, only show subjects with training_status === 'trained' (default: false)
 */
const CharacterPicker = ({
  value = [],
  onChange,
  multiple = true,
  kind = 'character',
  onlyTrained = false
}) => {
  const theme = useTheme();
  const [subjects, setSubjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [, setError] = useState(null);
  const [imgErrors, setImgErrors] = useState({});

  useEffect(() => {
    const fetchSubjects = async () => {
      setLoading(true);
      try {
        const data = await listCastLibrary();
        let filtered = data.subjects || [];
        
        if (kind) {
          filtered = filtered.filter(s => s.kind === kind);
        }
        
        if (onlyTrained) {
          filtered = filtered.filter(s => s.training_status === 'trained');
        }
        
        setSubjects(filtered);
      } catch (err) {
        console.error('Failed to fetch cast library:', err);
        setError('Failed to load characters');
      } finally {
        setLoading(false);
      }
    };

    fetchSubjects();
  }, [kind, onlyTrained]);

  const handleToggle = (id) => {
    if (!onChange) return;

    if (multiple) {
      if (value.includes(id)) {
        onChange(value.filter(v => v !== id));
      } else {
        onChange([...value, id]);
      }
    } else {
      onChange([id]);
    }
  };

  const getInitials = (name) => {
    return name
      .split(' ')
      .map((n) => n[0])
      .join('')
      .toUpperCase()
      .substring(0, 2);
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <CircularProgress size={24} />
      </Box>
    );
  }

  if (subjects.length === 0) {
    return (
      <Box sx={{ p: 2, textAlign: 'center' }}>
        <Typography variant="body2" color="text.secondary">
          No characters yet — train one in the Cast Library.
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
      {subjects.map((s) => {
        const isSelected = value.includes(s.id);
        const hasError = imgErrors[s.id];

        return (
          <Card
            key={s.id}
            onClick={() => handleToggle(s.id)}
            elevation={isSelected ? 4 : 0}
            sx={{
              width: 120,
              cursor: 'pointer',
              border: isSelected ? '2px solid' : '1px solid',
              borderColor: isSelected ? 'primary.main' : 'divider',
              m: isSelected ? 0 : '1px', // Compensate for border width difference
              display: 'flex',
              flexDirection: 'column',
              transition: 'all 0.1s ease-in-out',
              borderRadius: 2,
              overflow: 'hidden',
              '&:hover': {
                borderColor: isSelected ? 'primary.main' : 'primary.light',
                transform: 'translateY(-2px)',
                boxShadow: theme.shadows[isSelected ? 6 : 2]
              }
            }}
          >
            <Box sx={{ width: '100%', height: 120, position: 'relative', bgcolor: 'grey.100' }}>
              {!hasError ? (
                <Box
                  component="img"
                  src={`${API_BASE}/cast-library/subjects/${s.id}/preview`}
                  alt={s.name}
                  onError={() => setImgErrors(prev => ({ ...prev, [s.id]: true }))}
                  sx={{
                    width: '100%',
                    height: '100%',
                    objectFit: 'cover'
                  }}
                />
              ) : (
                <Box
                  sx={{
                    width: '100%',
                    height: '100%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    bgcolor: 'primary.light',
                    color: 'primary.contrastText'
                  }}
                >
                  <Typography variant="h5">
                    {getInitials(s.name)}
                  </Typography>
                </Box>
              )}
            </Box>
            
            <Box sx={{ p: 1, display: 'flex', flexDirection: 'column', gap: 0.5 }}>
              <Typography 
                variant="caption" 
                fontWeight="bold" 
                noWrap 
                sx={{ display: 'block' }}
                title={s.name}
              >
                {s.name}
              </Typography>

              {s.trigger_word && (
                <Typography 
                  variant="caption" 
                  sx={{ 
                    fontFamily: 'monospace', 
                    fontSize: '0.65rem', 
                    color: 'text.secondary',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap'
                  }}
                >
                  {s.trigger_word}
                </Typography>
              )}

              <Chip 
                label={s.training_status || 'untrained'} 
                size="small" 
                variant="filled"
                color={
                  s.training_status === 'trained' ? 'success' : 
                  s.training_status === 'training' ? 'warning' : 'default'
                }
                sx={{ height: 16, fontSize: '0.6rem', mt: 0.5 }}
              />
            </Box>
          </Card>
        );
      })}
    </Box>
  );
};

export default CharacterPicker;
