// frontend/src/components/layout/ProgressFooterBar.jsx
// The bar and main line follow the first running job; every other job in flight
// shows as a compact chip, and hovering or clicking lists them all.

import React, { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import { Box, LinearProgress, Popover, Typography, Divider, useTheme } from '@mui/material';
import { useUnifiedProgress } from '../../contexts/UnifiedProgressContext';
import TaskQueueIndicator from './TaskQueueIndicator';
import { useAppStore } from '../../stores/useAppStore';
import { navChromeWidth } from '../../config/navCatalog';
import { listActiveJobs } from '../../api/jobsService';
import {
    activeVideoJobs,
    buildFooterEntries,
    footerView,
    processType,
    statusLine,
    typeLabel,
} from './progressFooterModel';
import { useUiTranslation } from '../../i18n';

const VIDEO_SEED_REFRESH_MS = 30000;

/**
 * Video batches in flight from /api/jobs/active: read on connect so a batch that
 * started before this page loaded shows, and re-read while one is shown so a
 * batch whose finishing job:event was missed drops out.
 */
function useVideoJobSeed(connectionState, refreshWhile) {
    const [seed, setSeed] = useState(null);
    const load = useCallback(async () => {
        const fetchedAt = Date.now();
        try {
            const data = await listActiveJobs({ kinds: ['video_gen'], limit: 50 });
            setSeed({ fetchedAt, jobs: data?.jobs || [] });
        } catch {
            // The socket feed still drives the footer; the next read retries.
        }
    }, []);
    useEffect(() => {
        if (connectionState === 'connected') load();
    }, [connectionState, load]);
    useEffect(() => {
        if (!refreshWhile) return undefined;
        const id = setInterval(load, VIDEO_SEED_REFRESH_MS);
        return () => clearInterval(id);
    }, [refreshWhile, load]);
    return seed;
}

const ProgressFooterBar = () => {
    const { t, language } = useUiTranslation();
    const theme = useTheme();
    const { activeProcesses, unifiedJobs, connectionState } = useUnifiedProgress();

    const [videoSeedWanted, setVideoSeedWanted] = useState(false);
    const seed = useVideoJobSeed(connectionState, videoSeedWanted);
    const videoJobs = useMemo(() => activeVideoJobs(unifiedJobs, seed), [unifiedJobs, seed]);
    useEffect(() => setVideoSeedWanted(videoJobs.length > 0), [videoJobs.length]);

    const entries = useMemo(
        () => buildFooterEntries(activeProcesses.values(), videoJobs),
        [activeProcesses, videoJobs, language],
    );
    const view = footerView(entries);

    // After the last job ends, its outcome stays on the bar for a grace period.
    const [lingering, setLingering] = useState(null); // { progress, text, failed }
    const hideTimerRef = useRef(null);
    const wasActiveRef = useRef(false);
    // Read by the grace timer at fire time, not from the closure that set it.
    const activeRef = useRef(false);
    const lastViewRef = useRef(null);

    useEffect(() => {
        const active = entries.length > 0;
        activeRef.current = active;
        if (active) {
            wasActiveRef.current = true;
            lastViewRef.current = footerView(entries);
            if (hideTimerRef.current) {
                clearTimeout(hideTimerRef.current);
                hideTimerRef.current = null;
            }
            setLingering(null);
            return;
        }
        if (!wasActiveRef.current) return;
        wasActiveRef.current = false;

        // A failure outranks a completion: the reason has to be seen, not a 0 % bar.
        const finished = Array.from(activeProcesses.values());
        const errored = finished
            .filter((p) => p && p.status === 'error')
            .sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0))[0];
        const completed = finished.find((p) => p && (p.status === 'complete' || p.status === 'end'));

        let grace = 3000;
        const last = lastViewRef.current;
        let next = { progress: last?.progress ?? 0, text: last?.statusText || t('layout.progressFooter.idle'), failed: false };
        if (errored) {
            next = {
                progress: 0,
                failed: true,
                text: errored.message
                    ? t('layout.progressFooter.failedWithReason', { label: typeLabel(processType(errored)), reason: errored.message })
                    : t('layout.progressFooter.failedNoReason', { label: typeLabel(processType(errored)) }),
            };
            grace = 15000;
        } else if (completed) {
            next = { progress: 100, failed: false, text: t('layout.progressFooter.complete', { label: typeLabel(processType(completed)) }) };
        }
        setLingering(next);

        if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
        hideTimerRef.current = setTimeout(() => {
            if (!activeRef.current) setLingering(null);
            hideTimerRef.current = null;
        }, grace);
    }, [entries, activeProcesses]);

    useEffect(() => () => {
        if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    }, []);

    // Hover shows every job in flight; a click keeps the list open until dismissed.
    // It closes once nothing is in flight, so the next job never reopens it unanchored.
    const jobsRef = useRef(null);
    const [listOpen, setListOpen] = useState(false);
    const [listPinned, setListPinned] = useState(false);
    const closeList = () => {
        setListPinned(false);
        setListOpen(false);
    };
    const hasView = Boolean(view);
    useEffect(() => {
        if (!hasView) {
            setListPinned(false);
            setListOpen(false);
        }
    }, [hasView]);

    const sidebarExpanded = useAppStore((state) => state.sidebarExpanded);
    const navChrome = useAppStore((state) => state.navChrome);
    const drawerWidth = navChromeWidth(navChrome, sidebarExpanded);

    if (!view && !lingering) {
        return null;
    }

    const failed = !view && Boolean(lingering?.failed);
    const progress = view ? view.progress : lingering.progress;
    const statusText = view ? view.statusText : lingering.text;
    const itemCount = view ? view.itemCount : null;
    const chips = view ? view.chips : [];

    // "Video Gen · batch name — waiting"
    const entryHeading = (entry) => {
        const label = typeLabel(entry.type);
        const named = entry.name ? t('layout.progressFooter.entryNamed', { label, name: entry.name }) : label;
        return entry.waiting ? t('layout.progressFooter.entryWaiting', { entry: named }) : named;
    };

    try {
        return (
            <Box
                sx={{
                    position: 'fixed',
                    bottom: 0,
                    left: drawerWidth,
                    right: 0,
                    height: '24px',
                    zIndex: 9999,
                    backgroundColor: theme.palette.background.paper,
                    borderTop: `1px solid ${theme.palette.divider}`,
                    display: 'flex',
                    alignItems: 'center',
                    px: 2,
                    boxShadow: '0 -2px 8px rgba(0,0,0,0.1)',
                    transition: 'opacity 0.3s ease-in-out',
                }}
            >
                <LinearProgress
                    color={failed ? "error" : "info"}
                    variant="determinate"
                    value={progress}
                    sx={{
                        height: '4px',
                        flexGrow: 1,
                        mr: 2,
                        borderRadius: '2px',
                        backgroundColor: theme.palette.mode === 'dark' ? 'grey.800' : 'grey.300',
                        '& .MuiLinearProgress-bar': { borderRadius: '2px' },
                    }}
                />
                <Typography
                    variant="caption"
                    sx={{
                        color: failed ? 'error.main' : 'info.main',
                        fontSize: '0.65rem',
                        fontWeight: 500,
                        mr: 1,
                        minWidth: itemCount ? '60px' : '30px',
                        textAlign: 'right',
                    }}
                >
                    {itemCount
                        ? t('layout.progressFooter.itemCount', { current: itemCount.current, total: itemCount.total, percent: Math.round(progress) })
                        : `${Math.round(progress)}%`}
                </Typography>
                <Box
                    ref={jobsRef}
                    data-testid="footer-jobs"
                    role={view ? 'button' : undefined}
                    tabIndex={view ? 0 : undefined}
                    aria-label={view ? t('layout.progressFooter.showJobs') : undefined}
                    onMouseEnter={() => { if (view) setListOpen(true); }}
                    onMouseLeave={() => { if (!listPinned) setListOpen(false); }}
                    onClick={() => {
                        if (!view) return;
                        setListOpen(true);
                        setListPinned((p) => !p);
                    }}
                    sx={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 1,
                        minWidth: 0,
                        cursor: view ? 'pointer' : 'default',
                    }}
                >
                    <Typography
                        variant="caption"
                        sx={{
                            color: failed ? 'error.main' : 'text.secondary',
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            fontSize: '0.7rem',
                            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
                            fontWeight: 400,
                            letterSpacing: '0.02em',
                            minWidth: 0,
                        }}
                    >
                        {statusText}
                    </Typography>
                    {chips.map((chip) => (
                        <Typography
                            key={chip.key}
                            variant="caption"
                            sx={{
                                flexShrink: 0,
                                whiteSpace: 'nowrap',
                                fontSize: '0.65rem',
                                px: 0.75,
                                borderRadius: 1,
                                border: `1px solid ${theme.palette.divider}`,
                                color: chip.waiting ? 'text.disabled' : 'text.secondary',
                            }}
                        >
                            {chip.text}
                        </Typography>
                    ))}
                </Box>

                <Divider orientation="vertical" flexItem sx={{ mx: 1, height: 16, alignSelf: 'center' }} />
                <TaskQueueIndicator compact={true} />

                <Popover
                    open={Boolean(view && listOpen)}
                    anchorEl={() => jobsRef.current}
                    onClose={closeList}
                    anchorOrigin={{ vertical: 'top', horizontal: 'left' }}
                    transformOrigin={{ vertical: 'bottom', horizontal: 'left' }}
                    disableRestoreFocus
                    disableAutoFocus
                    disableEnforceFocus
                    disableScrollLock
                    sx={{ zIndex: 10000, pointerEvents: listPinned ? 'auto' : 'none' }}
                >
                    <Box sx={{ p: 1.5, maxWidth: 420 }}>
                        {(view?.entries || []).map((entry) => (
                            <Box key={entry.key} sx={{ mb: 1, '&:last-child': { mb: 0 } }}>
                                <Typography variant="caption" sx={{ fontWeight: 600, display: 'block' }}>
                                    {entryHeading(entry)}
                                </Typography>
                                <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                                    {statusLine(entry)}
                                    {!entry.waiting && entry.progress != null ? ` (${Math.round(entry.progress)}%)` : ''}
                                </Typography>
                            </Box>
                        ))}
                    </Box>
                </Popover>
            </Box>
        );
    } catch (error) {
        console.error('ProgressFooterBar rendering error:', error);
        return (
            <Box
                sx={{
                    position: 'fixed',
                    bottom: 0,
                    left: drawerWidth,
                    right: 0,
                    height: '24px',
                    backgroundColor: 'error.main',
                    display: 'flex',
                    alignItems: 'center',
                    px: 2,
                    zIndex: 9999,
                }}
            >
                <Typography variant="caption" color="white">
                    {t('layout.progressFooter.renderError')}
                </Typography>
            </Box>
        );
    }
};

export default ProgressFooterBar;
