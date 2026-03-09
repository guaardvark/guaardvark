// frontend/src/components/layout/TaskQueueIndicator.jsx
// Component to show task queue status in the footer bar
// Version 1.1 - Phase 3 with WebSocket integration

import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
    Box,
    Typography,
    Tooltip,
    IconButton,
    Badge,
    Popover,
    List,
    ListItem,
    ListItemText,
    ListItemIcon,
    Divider,
    LinearProgress,
    Chip,
    useTheme
} from '@mui/material';
import {
    Queue as QueueIcon,
    PlayArrow as RunningIcon,
    Schedule as ScheduledIcon,
    CheckCircle as CompletedIcon,
    Error as ErrorIcon,
    Refresh as RefreshIcon
} from '@mui/icons-material';
import { useUnifiedProgress } from '../../contexts/UnifiedProgressContext';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

const TaskQueueIndicator = ({ compact = true }) => {
    const theme = useTheme();
    const { socketRef, activeProcesses, globalProgress } = useUnifiedProgress();
    const [anchorEl, setAnchorEl] = useState(null);
    const [queueData, setQueueData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const refreshIntervalRef = useRef(null);
    const lastFetchRef = useRef(0);

    const fetchQueueSummary = useCallback(async (force = false) => {
        // Debounce rapid fetches unless forced
        const now = Date.now();
        if (!force && now - lastFetchRef.current < 2000) {
            return;
        }
        lastFetchRef.current = now;

        try {
            setLoading(true);
            setError(null);

            const response = await fetch(`${API_BASE}/scheduler/queue/summary`);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();
            if (data.success) {
                setQueueData(data.data);
            } else {
                setError(data.message || 'Failed to fetch queue data');
            }
        } catch (err) {
            console.error('TaskQueueIndicator: Failed to fetch queue summary:', err);
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }, []);

    // Initial fetch and periodic refresh
    useEffect(() => {
        fetchQueueSummary(true);

        // Refresh every 15 seconds (reduced from 10 since we have WebSocket updates)
        refreshIntervalRef.current = setInterval(() => fetchQueueSummary(false), 15000);

        return () => {
            if (refreshIntervalRef.current) {
                clearInterval(refreshIntervalRef.current);
            }
        };
    }, [fetchQueueSummary]);

    // Listen to WebSocket events for real-time updates
    useEffect(() => {
        const socket = socketRef?.current;
        if (!socket) return;

        // Refresh queue when task events occur
        const handleTaskEvent = () => {
            // Debounced fetch on task events
            fetchQueueSummary(false);
        };

        socket.on('task_created', handleTaskEvent);
        socket.on('task_cancelled', handleTaskEvent);
        socket.on('task_retried', handleTaskEvent);

        return () => {
            socket.off('task_created', handleTaskEvent);
            socket.off('task_cancelled', handleTaskEvent);
            socket.off('task_retried', handleTaskEvent);
        };
    }, [socketRef, fetchQueueSummary]);

    // Also refresh when active processes change significantly
    useEffect(() => {
        // Trigger fetch when processes complete or start
        if (globalProgress.active || activeProcesses.size > 0) {
            fetchQueueSummary(false);
        }
    }, [globalProgress.active, activeProcesses.size, fetchQueueSummary]);

    const handleClick = (event) => {
        setAnchorEl(event.currentTarget);
        // Refresh when opening popover
        fetchQueueSummary();
    };

    const handleClose = () => {
        setAnchorEl(null);
    };

    const open = Boolean(anchorEl);

    // Calculate badge count
    const totalActive = queueData?.queue_summary?.total_active || 0;
    const running = queueData?.queue_summary?.running || 0;
    const pending = queueData?.queue_summary?.pending || 0;

    // Get status color
    const getStatusColor = () => {
        if (running > 0) return theme.palette.info.main;
        if (pending > 0) return theme.palette.warning.main;
        return theme.palette.text.secondary;
    };

    // Compact view for footer bar
    if (compact) {
        return (
            <>
                <Tooltip title={`Task Queue: ${running} running, ${pending} pending`}>
                    <Box
                        onClick={handleClick}
                        sx={{
                            display: 'flex',
                            alignItems: 'center',
                            cursor: 'pointer',
                            px: 1,
                            py: 0.5,
                            borderRadius: 1,
                            '&:hover': {
                                backgroundColor: theme.palette.action.hover
                            }
                        }}
                    >
                        <Badge
                            badgeContent={totalActive}
                            color={running > 0 ? "info" : "default"}
                            max={99}
                            sx={{
                                '& .MuiBadge-badge': {
                                    fontSize: '0.6rem',
                                    minWidth: '14px',
                                    height: '14px',
                                    padding: '0 4px'
                                }
                            }}
                        >
                            <QueueIcon
                                sx={{
                                    fontSize: '1rem',
                                    color: getStatusColor()
                                }}
                            />
                        </Badge>
                        {running > 0 && (
                            <Typography
                                variant="caption"
                                sx={{
                                    ml: 0.5,
                                    fontSize: '0.65rem',
                                    color: theme.palette.info.main
                                }}
                            >
                                {running} running
                            </Typography>
                        )}
                    </Box>
                </Tooltip>

                <Popover
                    open={open}
                    anchorEl={anchorEl}
                    onClose={handleClose}
                    anchorOrigin={{
                        vertical: 'top',
                        horizontal: 'center',
                    }}
                    transformOrigin={{
                        vertical: 'bottom',
                        horizontal: 'center',
                    }}
                    PaperProps={{
                        sx: {
                            width: 320,
                            maxHeight: 400,
                            overflow: 'hidden'
                        }
                    }}
                >
                    <Box sx={{ p: 1.5 }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                            <Typography variant="subtitle2" fontWeight="bold">
                                Task Queue
                            </Typography>
                            <IconButton size="small" onClick={fetchQueueSummary} disabled={loading}>
                                <RefreshIcon sx={{ fontSize: '1rem' }} />
                            </IconButton>
                        </Box>

                        {loading && <LinearProgress sx={{ mb: 1 }} />}

                        {error && (
                            <Typography variant="caption" color="error" sx={{ display: 'block', mb: 1 }}>
                                {error}
                            </Typography>
                        )}

                        {queueData && (
                            <>
                                {/* Summary chips */}
                                <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mb: 1.5 }}>
                                    <Chip
                                        icon={<RunningIcon sx={{ fontSize: '0.9rem' }} />}
                                        label={`${running} Running`}
                                        size="small"
                                        color={running > 0 ? "info" : "default"}
                                        variant={running > 0 ? "filled" : "outlined"}
                                    />
                                    <Chip
                                        icon={<ScheduledIcon sx={{ fontSize: '0.9rem' }} />}
                                        label={`${pending} Pending`}
                                        size="small"
                                        color={pending > 0 ? "warning" : "default"}
                                        variant={pending > 0 ? "filled" : "outlined"}
                                    />
                                    <Chip
                                        icon={<CompletedIcon sx={{ fontSize: '0.9rem' }} />}
                                        label={`${queueData.queue_summary?.completed_today || 0} Today`}
                                        size="small"
                                        color="success"
                                        variant="outlined"
                                    />
                                    {(queueData.queue_summary?.failed_today || 0) > 0 && (
                                        <Chip
                                            icon={<ErrorIcon sx={{ fontSize: '0.9rem' }} />}
                                            label={`${queueData.queue_summary.failed_today} Failed`}
                                            size="small"
                                            color="error"
                                            variant="outlined"
                                        />
                                    )}
                                </Box>

                                <Divider sx={{ my: 1 }} />

                                {/* Running tasks */}
                                {queueData.running_tasks && queueData.running_tasks.length > 0 && (
                                    <>
                                        <Typography variant="caption" color="text.secondary" fontWeight="bold">
                                            Active Tasks
                                        </Typography>
                                        <List dense sx={{ py: 0 }}>
                                            {queueData.running_tasks.map((task) => (
                                                <ListItem key={task.id} sx={{ px: 0 }}>
                                                    <ListItemIcon sx={{ minWidth: 28 }}>
                                                        <RunningIcon
                                                            sx={{
                                                                fontSize: '1rem',
                                                                color: theme.palette.info.main
                                                            }}
                                                        />
                                                    </ListItemIcon>
                                                    <ListItemText
                                                        primary={
                                                            <Typography variant="caption" noWrap>
                                                                {task.handler_display || task.handler}
                                                            </Typography>
                                                        }
                                                        secondary={
                                                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                                                                <LinearProgress
                                                                    variant="determinate"
                                                                    value={task.progress || 0}
                                                                    sx={{
                                                                        flexGrow: 1,
                                                                        height: 3,
                                                                        borderRadius: 1
                                                                    }}
                                                                />
                                                                <Typography variant="caption" sx={{ fontSize: '0.6rem' }}>
                                                                    {task.progress || 0}%
                                                                </Typography>
                                                            </Box>
                                                        }
                                                    />
                                                </ListItem>
                                            ))}
                                        </List>
                                    </>
                                )}

                                {/* Next scheduled */}
                                {queueData.next_scheduled && (
                                    <>
                                        <Divider sx={{ my: 1 }} />
                                        <Typography variant="caption" color="text.secondary">
                                            Next scheduled: {queueData.next_scheduled.handler} at{' '}
                                            {new Date(queueData.next_scheduled.next_run_at).toLocaleTimeString()}
                                        </Typography>
                                    </>
                                )}

                                {/* Handler counts */}
                                {Object.keys(queueData.handler_counts || {}).length > 0 && (
                                    <>
                                        <Divider sx={{ my: 1 }} />
                                        <Typography variant="caption" color="text.secondary" fontWeight="bold">
                                            By Handler
                                        </Typography>
                                        <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 0.5 }}>
                                            {Object.entries(queueData.handler_counts).map(([handler, count]) => (
                                                <Chip
                                                    key={handler}
                                                    label={`${handler}: ${count}`}
                                                    size="small"
                                                    variant="outlined"
                                                    sx={{ fontSize: '0.65rem' }}
                                                />
                                            ))}
                                        </Box>
                                    </>
                                )}

                                {/* Empty state */}
                                {totalActive === 0 && (
                                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', textAlign: 'center', py: 2 }}>
                                        No active tasks in queue
                                    </Typography>
                                )}
                            </>
                        )}
                    </Box>
                </Popover>
            </>
        );
    }

    // Full view (for TaskPage or dashboard)
    return (
        <Box sx={{ p: 2 }}>
            <Typography variant="h6" gutterBottom>
                Task Queue
            </Typography>
            {/* Full view implementation would go here */}
        </Box>
    );
};

export default TaskQueueIndicator;
