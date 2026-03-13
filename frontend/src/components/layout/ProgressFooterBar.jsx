// frontend/src/components/layout/ProgressFooterBar.jsx
// Version 3.0: Fixed premature clearing, added grace period, improved all-process-type support

import React, { useState, useRef, useEffect } from 'react';
import { Box, LinearProgress, Typography, Divider, useTheme } from '@mui/material';
import { useUnifiedProgress } from '../../contexts/UnifiedProgressContext';
import TaskQueueIndicator from './TaskQueueIndicator';
import { useAppStore } from '../../stores/useAppStore';

// Friendly labels for process types
const PROCESS_TYPE_LABELS = {
    indexing: 'Indexing',
    image_generation: 'Image Gen',
    csv_processing: 'CSV Gen',
    file_generation: 'File Gen',
    analysis: 'Analysis',
    upload: 'Upload',
    llm_processing: 'LLM',
    web_scraping: 'Web Scrape',
    backup: 'Backup',
    training: 'Training',
    task_processing: 'Task',
    voice_processing: 'Voice',
    document_processing: 'Documents',
    wordpress_pull: 'WP Pull',
    wordpress_push: 'WP Push',
    wordpress_processing: 'WordPress',
    processing: 'Processing',
    unknown: 'Working',
};

const ProgressFooterBar = () => {
    const theme = useTheme();
    const { globalProgress, activeProcesses } = useUnifiedProgress();

    // UI state
    const [visible, setVisible] = useState(false);
    const [progress, setProgress] = useState(0);
    const [statusText, setStatusText] = useState('Idle');
    const [itemCount, setItemCount] = useState(null); // e.g., "3 of 10"

    // Refs for tracking
    const lastProcessIdRef = useRef(null);
    const hideTimerRef = useRef(null);
    const lastActiveRef = useRef(false);

    // Core effect: sync footer state from unified progress
    useEffect(() => {
        const hasActive = globalProgress.active && globalProgress.activeCount > 0;

        // Cancel any pending hide timer when new activity arrives
        if (hasActive && hideTimerRef.current) {
            clearTimeout(hideTimerRef.current);
            hideTimerRef.current = null;
        }

        if (hasActive) {
            lastActiveRef.current = true;
            setVisible(true);

            // Get all non-terminal processes sorted by priority then recency
            const processes = Array.from(activeProcesses.values()).filter(
                p => p && p.status !== 'complete' && p.status !== 'end' && p.status !== 'error' && p.status !== 'cancelled'
            );

            if (processes.length === 0) return;

            // Priority order for selecting which process to display
            const priorityOrder = [
                'indexing', 'image_generation', 'csv_processing', 'file_generation',
                'analysis', 'upload', 'llm_processing', 'web_scraping', 'backup',
                'training', 'task_processing', 'voice_processing', 'document_processing',
                'wordpress_pull', 'wordpress_push', 'wordpress_processing', 'processing',
            ];

            let current = null;
            for (const pt of priorityOrder) {
                current = processes.find(p =>
                    (p.processType === pt || p.process_type === pt)
                );
                if (current) break;
            }

            // Fallback: most recent process
            if (!current) {
                current = processes.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0))[0];
            }

            if (!current) return;

            // Track process switch
            if (current.job_id !== lastProcessIdRef.current) {
                lastProcessIdRef.current = current.job_id;
            }

            // Progress value
            const pct = typeof current.progress === 'number' ? Math.max(0, Math.min(100, current.progress)) : 0;
            setProgress(pct);

            // Item counts from additional_data
            const ad = current.additional_data || {};
            if (ad.generated_count != null && ad.target_count != null) {
                setItemCount({ current: ad.generated_count, total: ad.target_count });
            } else {
                setItemCount(null);
            }

            // Status text: use the process message, prefix with type label if not already present
            const typeKey = current.processType || current.process_type || 'processing';
            const label = PROCESS_TYPE_LABELS[typeKey] || typeKey;
            const msg = current.message || 'Processing...';
            // Only prefix if the message doesn't already mention the type
            const msgLower = msg.toLowerCase();
            const needsPrefix = !msgLower.includes(typeKey.replace('_', ' ')) &&
                                !msgLower.includes(label.toLowerCase());
            setStatusText(needsPrefix ? `${label}: ${msg}` : msg);

        } else if (lastActiveRef.current) {
            // Was active, now transitioning to idle
            // Show completion state briefly, then hide with a grace period
            lastActiveRef.current = false;

            // Check if there are completed processes to show final state
            const completedProcess = Array.from(activeProcesses.values()).find(
                p => p && (p.status === 'complete' || p.status === 'end')
            );

            if (completedProcess) {
                setProgress(100);
                const typeKey = completedProcess.processType || completedProcess.process_type || 'processing';
                const label = PROCESS_TYPE_LABELS[typeKey] || typeKey;
                setStatusText(`${label}: Complete`);
            }

            // Grace period: keep the bar visible for 3 seconds after last activity
            // This prevents flicker from brief state transitions
            if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
            hideTimerRef.current = setTimeout(() => {
                // Double-check: only hide if still no active progress
                if (!globalProgress.active) {
                    setVisible(false);
                    setProgress(0);
                    setStatusText('Idle');
                    setItemCount(null);
                    lastProcessIdRef.current = null;
                }
                hideTimerRef.current = null;
            }, 3000);

        } else if (!visible) {
            // Fully idle state — nothing to do
        }
    }, [globalProgress, activeProcesses]);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (hideTimerRef.current) {
                clearTimeout(hideTimerRef.current);
                hideTimerRef.current = null;
            }
        };
    }, []);

    // Dynamic sidebar width
    const sidebarExpanded = useAppStore((state) => state.sidebarExpanded);
    const drawerWidth = sidebarExpanded ? 240 : 64;

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
                    opacity: visible ? 1 : 0.2,
                    pointerEvents: visible ? 'auto' : 'none',
                    transition: 'opacity 0.3s ease-in-out',
                }}
            >
                {visible ? (
                    <>
                        <LinearProgress
                            color="info"
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
                        {itemCount ? (
                            <Typography
                                variant="caption"
                                sx={{
                                    color: 'info.main',
                                    fontSize: '0.65rem',
                                    fontWeight: 500,
                                    mr: 1,
                                    minWidth: '60px',
                                    textAlign: 'right',
                                }}
                            >
                                {itemCount.current} of {itemCount.total} ({Math.round(progress)}%)
                            </Typography>
                        ) : (
                            <Typography
                                variant="caption"
                                sx={{
                                    color: 'info.main',
                                    fontSize: '0.65rem',
                                    fontWeight: 500,
                                    mr: 1,
                                    minWidth: '30px',
                                    textAlign: 'right',
                                }}
                            >
                                {Math.round(progress)}%
                            </Typography>
                        )}
                    </>
                ) : (
                    <Box
                        sx={{
                            height: '4px',
                            flexGrow: 1,
                            mr: 2,
                            backgroundColor: theme.palette.mode === 'dark' ? 'grey.800' : 'grey.300',
                            borderRadius: '2px',
                        }}
                    />
                )}
                <Typography
                    variant="caption"
                    sx={{
                        color: 'text.secondary',
                        whiteSpace: 'nowrap',
                        fontSize: '0.7rem',
                        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
                        fontWeight: 400,
                        letterSpacing: '0.02em',
                        flexGrow: 0,
                        mr: 1,
                    }}
                >
                    {statusText}
                </Typography>

                <Divider orientation="vertical" flexItem sx={{ mx: 1, height: 16, alignSelf: 'center' }} />
                <TaskQueueIndicator compact={true} />
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
                    Progress bar error - check console
                </Typography>
            </Box>
        );
    }
};

export default ProgressFooterBar;
