// frontend/src/components/modals/ImageModelsModal.jsx
import React, { useState, useEffect } from "react";
import {
    Dialog,
    DialogTitle,
    DialogContent,
    DialogActions,
    Button,
    List,
    ListItem,
    ListItemText,
    ListItemIcon,
    Typography,
    CircularProgress,
    Box,
    Chip,
    LinearProgress,
} from "@mui/material";
import ImageIcon from "@mui/icons-material/Image";
import CloudDownloadIcon from "@mui/icons-material/CloudDownload";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorIcon from "@mui/icons-material/Error";
import LockIcon from "@mui/icons-material/Lock";
import axios from "axios";

const ImageModelsModal = ({ open, onClose, showMessage }) => {
    const [models, setModels] = useState([]);
    const [loading, setLoading] = useState(true);
    const [downloadStatus, setDownloadStatus] = useState({
        is_downloading: false,
        current_model: null,
        progress: 0,
        status: "idle",
    });
    const [error, setError] = useState(null);

    const fetchModels = async () => {
        try {
            setLoading(true);
            const res = await axios.get("/api/batch-image/models");
            if (res.data.success) {
                setModels(res.data.data.models);
            } else {
                setError("Failed to load models");
            }
        } catch (err) {
            setError(err.message || "Error fetching models");
        } finally {
            setLoading(false);
        }
    };

    const fetchDownloadStatus = async () => {
        try {
            const res = await axios.get("/api/batch-image/models/download-status");
            if (res.data.success) {
                setDownloadStatus(res.data.data);
                if (["completed", "failed"].includes(res.data.data.status) && res.data.data.is_downloading === false) {
                    // Refetch models if download finished to update the "installed" status
                    if (res.data.data.status === "completed") {
                        showMessage("Model download completed successfully!", "success");
                        fetchModels();
                    } else if (res.data.data.status === "failed") {
                        showMessage(`Model download failed: ${res.data.data.error}`, "error");
                    }
                    // Stop polling by clearing current model status locally if it's done
                    setDownloadStatus(prev => ({ ...prev, status: "idle", current_model: null }));
                }
            }
        } catch (err) {
            console.error("Failed to fetch download status", err);
        }
    };

    useEffect(() => {
        if (open) {
            fetchModels();
            fetchDownloadStatus();
        } else {
            setModels([]);
            setError(null);
        }
    }, [open]);

    useEffect(() => {
        let interval;
        if (open && downloadStatus.is_downloading) {
            interval = setInterval(fetchDownloadStatus, 2000); // poll every 2s
        }
        return () => clearInterval(interval);
    }, [open, downloadStatus.is_downloading]);

    const handleDownload = async (model_path) => {
        try {
            const res = await axios.post("/api/batch-image/models/download", { model_path });
            if (res.data.success) {
                showMessage(`Started downloading ${model_path}. This may take a while.`, "info");
                setDownloadStatus({
                    is_downloading: true,
                    current_model: model_path,
                    progress: 0,
                    status: "starting"
                });
            } else {
                showMessage(res.data.error || "Failed to start download", "error");
            }
        } catch (err) {
            if (err.response && err.response.status === 409) {
                showMessage("A download is already in progress.", "warning");
            } else {
                showMessage(err.message || "Error starting download", "error");
            }
        }
    };

    return (
        <Dialog open={open} onClose={() => !downloadStatus.is_downloading && onClose()} maxWidth="sm" fullWidth>
            <DialogTitle>Manage Image Generation Models</DialogTitle>
            <DialogContent dividers>
                {error && (
                    <Box mb={2}>
                        <Typography color="error">{error}</Typography>
                    </Box>
                )}

                {loading ? (
                    <Box display="flex" justifyContent="center" p={3}>
                        <CircularProgress />
                    </Box>
                ) : (
                    <List>
                        {models.map((model) => (
                            <ListItem key={model.id} divider>
                                <ListItemIcon>
                                    <ImageIcon color={model.is_downloaded ? "primary" : "action"} />
                                </ListItemIcon>
                                <ListItemText
                                    primary={model.name || model.id}
                                    secondary={model.path}
                                />

                                {downloadStatus.is_downloading && downloadStatus.current_model === model.path ? (
                                    <Box sx={{ width: '100px', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                                        <Typography variant="caption">{downloadStatus.status} ({downloadStatus.progress}%)</Typography>
                                        <LinearProgress variant={downloadStatus.progress > 0 ? "determinate" : "indeterminate"} value={downloadStatus.progress} sx={{ width: '100%' }} />
                                    </Box>
                                ) : model.is_downloaded ? (
                                    <Chip
                                        icon={<CheckCircleIcon />}
                                        label="Installed"
                                        color="success"
                                        size="small"
                                        variant="outlined"
                                    />
                                ) : (
                                    <Button
                                        variant="outlined"
                                        size="small"
                                        startIcon={<CloudDownloadIcon />}
                                        onClick={() => handleDownload(model.path)}
                                        disabled={downloadStatus.is_downloading}
                                    >
                                        Install
                                    </Button>
                                )}
                            </ListItem>
                        ))}
                        {models.length === 0 && !loading && (
                            <Typography variant="body2" color="textSecondary" align="center" sx={{ py: 3 }}>
                                No models available in configuration.
                            </Typography>
                        )}
                    </List>
                )}
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose} disabled={downloadStatus.is_downloading}>
                    {downloadStatus.is_downloading ? "Downloading..." : "Close"}
                </Button>
            </DialogActions>
        </Dialog>
    );
};

export default ImageModelsModal;
