import React, { useEffect, useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  List,
  ListItem,
  ListItemText,
  IconButton,
  Tooltip,
  Typography,
  Box,
  Chip,
} from "@mui/material";
import RestoreIcon from "@mui/icons-material/Restore";
import DeleteIcon from "@mui/icons-material/Delete";
import DownloadIcon from "@mui/icons-material/Download";

const ManageBackupsModal = ({
  open,
  onClose,
  onRestore,
  onDelete,
  onDownload,
  onRefresh,
  backups = [],
}) => {
  const [backupInfo, setBackupInfo] = useState({});

  useEffect(() => {
    // Extract basic info from backup filenames
    const info = {};
    backups.forEach(backup => {
      const name = backup;
      const isSystemBackup = name.includes('system_backup');
      const isTimestampBackup = name.includes('backup_') && name.includes('_');
      
      if (isSystemBackup) {
        // Extract date from system backup format
        const dateMatch = name.match(/(\d{8})_(\d{6})/);
        if (dateMatch) {
          const dateStr = dateMatch[1];
          const timeStr = dateMatch[2];
          const date = new Date(
            parseInt(dateStr.substring(0, 4)),
            parseInt(dateStr.substring(4, 6)) - 1,
            parseInt(dateStr.substring(6, 8)),
            parseInt(timeStr.substring(0, 2)),
            parseInt(timeStr.substring(2, 4)),
            parseInt(timeStr.substring(4, 6))
          );
          info[name] = {
            type: 'System Backup',
            date: date.toLocaleDateString(),
            time: date.toLocaleTimeString(),
            description: 'Complete system backup including all data and files'
          };
        }
      } else if (isTimestampBackup) {
        // Extract timestamp from backup format
        const timestampMatch = name.match(/backup_(\d+)/);
        if (timestampMatch) {
          const timestamp = parseInt(timestampMatch[1]) / 1000000; // Convert nanoseconds to seconds
          const date = new Date(timestamp * 1000);
          info[name] = {
            type: 'Data Backup',
            date: date.toLocaleDateString(),
            time: date.toLocaleTimeString(),
            description: 'Application data backup'
          };
        }
      } else {
        info[name] = {
          type: 'Unknown',
          date: 'Unknown',
          time: '',
          description: 'Backup file'
        };
      }
    });
    setBackupInfo(info);
  }, [backups]);

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>Manage Backups</DialogTitle>
      <DialogContent dividers>
        {backups.length === 0 ? (
          <Box sx={{ textAlign: 'center', py: 4 }}>
            <Typography variant="body1" color="text.secondary">
              No backups found
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              Create a backup first to see it here
            </Typography>
          </Box>
        ) : (
          <List>
            {backups.map((name) => {
              const info = backupInfo[name] || {};
              return (
                <ListItem
                  key={name}
                  sx={{
                    border: 1,
                    borderColor: 'divider',
                    borderRadius: 1,
                    mb: 1,
                    flexDirection: { xs: 'column', sm: 'row' },
                    alignItems: { xs: 'flex-start', sm: 'center' }
                  }}
                  secondaryAction={
                    <Box sx={{ display: 'flex', gap: 1, mt: { xs: 1, sm: 0 } }}>
                      <Tooltip title="Download">
                        <IconButton
                          onClick={() => onDownload && onDownload(name)}
                          color="secondary"
                        >
                          <DownloadIcon />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Restore">
                        <IconButton
                          onClick={() => onRestore && onRestore(name)}
                          color="primary"
                        >
                          <RestoreIcon />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Delete">
                        <IconButton
                          onClick={() => onDelete && onDelete(name)}
                          color="error"
                        >
                          <DeleteIcon />
                        </IconButton>
                      </Tooltip>
                    </Box>
                  }
                >
                  <ListItemText
                    primary={
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Typography variant="subtitle1" component="span">
                          {name}
                        </Typography>
                        <Chip 
                          label={info.type} 
                          size="small" 
                          color={info.type === 'System Backup' ? 'primary' : 'default'}
                        />
                      </Box>
                    }
                    secondary={
                      <Box sx={{ mt: 1 }}>
                        <Typography variant="body2" color="text.secondary" component="span" sx={{ display: 'block' }}>
                          <strong>Date:</strong> {info.date} {info.time && `at ${info.time}`}
                        </Typography>
                        <Typography variant="body2" color="text.secondary" component="span" sx={{ display: 'block' }}>
                          {info.description}
                        </Typography>
                      </Box>
                    }
                  />
                </ListItem>
              );
            })}
          </List>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onRefresh}>Refresh</Button>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
};

export default ManageBackupsModal;
