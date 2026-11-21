import React from 'react';
import { useNavigate } from 'react-router-dom';
import CastLibraryView from '../components/filmcrew/CastLibraryView';
import PageLayout from '../components/layout/PageLayout';
import { useUiTranslation } from '../i18n';

// Cast & LoRA Studio — the standalone home for all character/LoRA management.
// Lifts the cast grid out of the Film Crew page and routes each card to a
// full-page detail (/cast/:subjectId) where you edit, generate samples, and
// train. Other pages link here rather than embedding cast UI of their own.
const CastStudioPage = () => {
  const { t } = useUiTranslation();
  const navigate = useNavigate();
  return (
    <PageLayout title={t('cast.studio.title')} variant="standard">
      <CastLibraryView asCards intro={t('cast.studio.intro')} onOpenSubject={(s) => navigate(`/cast/${s.id}`)} />
    </PageLayout>
  );
};

export default CastStudioPage;
