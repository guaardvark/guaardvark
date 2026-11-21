// frontend/src/components/layout/ScheduledFooterChip.jsx
// The footer's count of jobs that have not started: held for the night shift
// or for a time. It opens the Jobs page, where they are listed and ordered.
import React from 'react';
import PropTypes from 'prop-types';
import { Link, Tooltip } from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';
import NightlightOutlinedIcon from '@mui/icons-material/NightlightOutlined';
import { useUiTranslation } from '../../i18n';

const ScheduledFooterChip = ({ count }) => {
    const { t } = useUiTranslation();
    if (!count) return null;
    return (
        <Tooltip title={t('jobs.nightShift.footerHelp')}>
            <Link
                component={RouterLink}
                to="/tasks"
                underline="hover"
                data-testid="footer-scheduled"
                sx={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 0.5,
                    flexShrink: 0,
                    whiteSpace: 'nowrap',
                    fontSize: '0.65rem',
                    color: 'text.secondary',
                }}
            >
                <NightlightOutlinedIcon sx={{ fontSize: 12 }} />
                {t('jobs.nightShift.footer', { count })}
            </Link>
        </Tooltip>
    );
};

ScheduledFooterChip.propTypes = { count: PropTypes.number };

export default ScheduledFooterChip;
