import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Typography } from '@mui/material';
import CastLibraryView from '../components/filmcrew/CastLibraryView';
import { useUiTranslation } from '../i18n';

// Cast & LoRA Studio — the standalone home for all character/LoRA management.
// Lifts the cast grid out of the Film Crew page and routes each card to a
// full-page detail (/cast/:subjectId) where you edit, generate samples, and
// train. Other pages link here rather than embedding cast UI of their own.
const CastStudioPage = () => {
  const { t } = useUiTranslation();
  const navigate = useNavigate();
  return (
    <Box sx={{ p: { xs: 1, sm: 2 } }}>
      <Box sx={{ px: 2, pt: 1 }}>
        <Typography variant="h4" sx={{ fontWeight: 600 }}>{t('cast.studio.title')}</Typography>
        <Typography variant="body2" color="text.secondary">
          {t('cast.studio.intro')}
        </Typography>
      </Box>
      <CastLibraryView onOpenSubject={(s) => navigate(`/cast/${s.id}`)} />
    </Box>
  );
};

export default CastStudioPage;
