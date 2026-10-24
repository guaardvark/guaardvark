import React from 'react';
import { Box, Chip, IconButton, Typography } from '@mui/material';
import {
  Download as DownloadIcon,
  Movie as MovieIcon,
  PlayArrow as PlayArrowIcon,
} from '@mui/icons-material';
import { useUiTranslation } from '../../i18n';

/**
 * A Film Crew film in the Media Library's Videos grid, styled like the Video Gen
 * batch cards beside it. The poster is the film's first storyboard frame.
 */
const FilmCard = React.memo(function FilmCard({ film, index, dateStr, onPlay, onDownload }) {
  const { t } = useUiTranslation();
  return (
    <Box
      onClick={() => onPlay(index)}
      sx={{
        cursor: 'pointer',
        position: 'relative',
        borderRadius: 2,
        overflow: 'visible',
        transition: 'transform 0.2s, box-shadow 0.2s',
        '&:hover': {
          transform: 'translateY(-3px)',
          boxShadow: 4,
          '& .film-play': { opacity: 1 },
        },
      }}
    >
      <Box sx={{
        position: 'relative', aspectRatio: '16/9',
        bgcolor: 'grey.900', borderRadius: 1.5, overflow: 'hidden',
        border: 1, borderColor: 'grey.700',
      }}>
        {film.poster_url ? (
          <Box
            component="img"
            src={film.poster_url}
            alt={film.name}
            sx={{ width: '100%', height: '100%', objectFit: 'cover' }}
            onError={(e) => { e.target.style.display = 'none'; }}
          />
        ) : (
          <Box sx={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <MovieIcon sx={{ fontSize: 32, color: 'grey.600' }} />
          </Box>
        )}
        <Box className="film-play" sx={{
          position: 'absolute', inset: 0,
          bgcolor: 'rgba(0,0,0,0.45)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          opacity: 0, transition: 'opacity 0.2s',
        }}>
          <PlayArrowIcon sx={{ fontSize: 36, color: 'white' }} />
        </Box>
        <Chip
          icon={<MovieIcon sx={{ fontSize: '0.75rem !important', color: 'white !important' }} />}
          label={t('images.film.badge')}
          size="small"
          sx={{
            position: 'absolute', top: 4, right: 4,
            height: 18, fontSize: '0.6rem',
            bgcolor: 'rgba(0,0,0,0.7)', color: 'white',
            '& .MuiChip-label': { px: 0.5 },
          }}
        />
      </Box>
      <Box sx={{ pt: 0.5, px: 0.25 }}>
        <Typography variant="caption" noWrap sx={{ fontWeight: 500, display: 'block', fontSize: '0.7rem' }}>
          {film.name}
        </Typography>
        {dateStr && (
          <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.6rem' }}>
            {dateStr}
          </Typography>
        )}
      </Box>
      <Box sx={{
        position: 'absolute', top: -8, right: -8,
        display: 'flex', gap: 0.25,
        opacity: 0, transition: 'opacity 0.2s',
        '.MuiBox-root:hover > &': { opacity: 1 },
      }}>
        <IconButton size="small" aria-label={t('images.film.download')} onClick={(e) => { e.stopPropagation(); onDownload(film); }}
          sx={{ bgcolor: 'background.paper', boxShadow: 2, p: 0.25, '&:hover': { bgcolor: 'primary.dark' } }}>
          <DownloadIcon sx={{ fontSize: 14 }} />
        </IconButton>
      </Box>
    </Box>
  );
});

export default FilmCard;
