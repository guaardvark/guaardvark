// frontend/src/components/filesystem/CSVSpreadsheetViewer.jsx
// CSV spreadsheet viewer with editable cells for DocumentsPage

import React, { useState, useEffect, useCallback } from 'react';
import {
  Box,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Button,
  Typography,
  IconButton,
  Tooltip,
  Alert,
  CircularProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  Chip,
} from '@mui/material';
import {
  Save as SaveIcon,
  Close as CloseIcon,
  Edit as EditIcon,
  Add as AddIcon,
  Delete as DeleteIcon,
  Download as DownloadIcon,
} from '@mui/icons-material';
import axios from 'axios';
import { BASE_URL } from '../../api/apiClient';

const CSVSpreadsheetViewer = ({ fileData, onClose, onSave }) => {
  const [csvData, setCsvData] = useState([]);
  const [headers, setHeaders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [editingCell, setEditingCell] = useState(null);
  const [hasChanges, setHasChanges] = useState(false);
  const [saving, setSaving] = useState(false);
  const [_originalData, setOriginalData] = useState([]);

  // Parse CSV content
  const parseCSV = useCallback((csvContent) => {
    const lines = csvContent.split('\n').filter(line => line.trim());
    if (lines.length === 0) return { headers: [], data: [] };

    // Parse CSV with proper quote handling
    const parseCSVLine = (line) => {
      const result = [];
      let current = '';
      let inQuotes = false;
      
      for (let i = 0; i < line.length; i++) {
        const char = line[i];
        
        if (char === '"') {
          if (inQuotes && line[i + 1] === '"') {
            // Escaped quote
            current += '"';
            i++; // Skip next quote
          } else {
            // Toggle quote state
            inQuotes = !inQuotes;
          }
        } else if (char === ',' && !inQuotes) {
          result.push(current.trim());
          current = '';
        } else {
          current += char;
        }
      }
      
      result.push(current.trim());
      return result;
    };

    const headers = parseCSVLine(lines[0]);
    const data = lines.slice(1).map(line => {
      const values = parseCSVLine(line);
      const row = {};
      headers.forEach((header, index) => {
        row[header] = values[index] || '';
      });
      return row;
    });

    return { headers, data };
  }, []);

  // Load CSV data
  useEffect(() => {
    const loadCSVData = async () => {
      if (!fileData?.id) return;

      setLoading(true);
      setError(null);

      try {
        const response = await axios.get(`${BASE_URL}/files/document/${fileData.id}/download`, {
          responseType: 'text',
        });

        const { headers, data } = parseCSV(response.data);
        setHeaders(headers);
        setCsvData(data);
        setOriginalData(JSON.parse(JSON.stringify(data))); // Deep copy
      } catch (err) {
        setError(err.response?.data?.message || 'Failed to load CSV data');
      } finally {
        setLoading(false);
      }
    };

    loadCSVData();
  }, [fileData, parseCSV]);

  // Handle cell edit
  const handleCellEdit = (rowIndex, header, value) => {
    const newData = [...csvData];
    newData[rowIndex][header] = value;
    setCsvData(newData);
    setHasChanges(true);
  };

  // Handle cell click to start editing
  const handleCellClick = (rowIndex, header) => {
    setEditingCell({ rowIndex, header });
  };

  // Handle cell blur to stop editing
  const handleCellBlur = () => {
    setEditingCell(null);
  };

  // Add new row
  const handleAddRow = () => {
    const newRow = {};
    headers.forEach(header => {
      newRow[header] = '';
    });
    setCsvData([...csvData, newRow]);
    setHasChanges(true);
  };

  // Delete row
  const handleDeleteRow = (rowIndex) => {
    const newData = csvData.filter((_, index) => index !== rowIndex);
    setCsvData(newData);
    setHasChanges(true);
  };

  // Save changes
  const handleSave = async () => {
    setSaving(true);
    try {
      // Convert data back to CSV format
      const csvContent = [
        headers.join(','),
        ...csvData.map(row => 
          headers.map(header => {
            const value = row[header] || '';
            // Escape quotes and wrap in quotes if contains comma, quote, or newline
            if (value.includes('"') || value.includes(',') || value.includes('\n')) {
              return `"${value.replace(/"/g, '""')}"`;
            }
            return value;
          }).join(',')
        )
      ].join('\n');

      // Save the updated CSV content
      const formData = new FormData();
      const blob = new Blob([csvContent], { type: 'text/csv' });
      formData.append('file', blob, fileData.filename);
      formData.append('folder_path', '/');

      await axios.post(`${BASE_URL}/files/upload`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      setHasChanges(false);
      setOriginalData(JSON.parse(JSON.stringify(csvData)));
      
      if (onSave) {
        onSave(fileData.id, csvData);
      }
    } catch (err) {
      setError(err.response?.data?.message || 'Failed to save CSV data');
    } finally {
      setSaving(false);
    }
  };

  // Download CSV
  const handleDownload = () => {
    const csvContent = [
      headers.join(','),
      ...csvData.map(row => 
        headers.map(header => {
          const value = row[header] || '';
          if (value.includes('"') || value.includes(',') || value.includes('\n')) {
            return `"${value.replace(/"/g, '""')}"`;
          }
          return value;
        }).join(',')
      )
    ].join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', fileData.filename);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (error) {
    return (
      <Alert severity="error" sx={{ m: 2 }}>
        {error}
      </Alert>
    );
  }

  return (
    <Dialog
      open={true}
      onClose={onClose}
      maxWidth="xl"
      fullWidth
      PaperProps={{
        sx: { height: '90vh' }
      }}
    >
      <DialogTitle>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <EditIcon />
            <Typography variant="h6">
              {fileData.filename} - Spreadsheet View
            </Typography>
            {hasChanges && (
              <Chip label="Unsaved Changes" color="warning" size="small" />
            )}
          </Box>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Tooltip title="Download CSV">
              <IconButton onClick={handleDownload} size="small">
                <DownloadIcon />
              </IconButton>
            </Tooltip>
            <Tooltip title="Close">
              <IconButton onClick={onClose} size="small">
                <CloseIcon />
              </IconButton>
            </Tooltip>
          </Box>
        </Box>
      </DialogTitle>

      <DialogContent sx={{ p: 0, overflow: 'hidden' }}>
        <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
          {/* Toolbar */}
          <Paper sx={{ p: 2, mb: 2, borderRadius: 0 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Typography variant="body2" color="text.secondary">
                {csvData.length} rows × {headers.length} columns
              </Typography>
              <Box sx={{ display: 'flex', gap: 1 }}>
                <Button
                  variant="outlined"
                  startIcon={<AddIcon />}
                  onClick={handleAddRow}
                  size="small"
                >
                  Add Row
                </Button>
                <Button
                  variant="contained"
                  startIcon={<SaveIcon />}
                  onClick={handleSave}
                  disabled={!hasChanges || saving}
                  size="small"
                >
                  {saving ? 'Saving...' : 'Save Changes'}
                </Button>
              </Box>
            </Box>
          </Paper>

          {/* Spreadsheet */}
          <Paper sx={{ flex: 1, overflow: 'auto' }}>
            <TableContainer>
              <Table stickyHeader>
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ minWidth: 50 }}>
                      #
                    </TableCell>
                    {headers.map((header, index) => (
                      <TableCell
                        key={index}
                        sx={{
                          minWidth: 150,
                          fontWeight: 'bold',
                        }}
                      >
                        {header}
                      </TableCell>
                    ))}
                    <TableCell sx={{ minWidth: 80 }}>
                      Actions
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {csvData.map((row, rowIndex) => (
                    <TableRow key={rowIndex} hover>
                      <TableCell>
                        {rowIndex + 1}
                      </TableCell>
                      {headers.map((header, colIndex) => {
                        const isEditing = editingCell?.rowIndex === rowIndex && editingCell?.header === header;
                        const cellKey = `${rowIndex}-${colIndex}`;
                        
                        return (
                          <TableCell
                            key={cellKey}
                            sx={{
                              p: 0,
                              cursor: 'pointer',
                            }}
                            onClick={() => handleCellClick(rowIndex, header)}
                          >
                            {isEditing ? (
                              <TextField
                                value={row[header] || ''}
                                onChange={(e) => handleCellEdit(rowIndex, header, e.target.value)}
                                onBlur={handleCellBlur}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') {
                                    handleCellBlur();
                                  }
                                }}
                                variant="outlined"
                                size="small"
                                fullWidth
                                multiline
                                maxRows={3}
                                autoFocus
                                sx={{
                                  '& .MuiOutlinedInput-root': {
                                    borderRadius: 0,
                                    '& fieldset': { border: 'none' }
                                  }
                                }}
                              />
                            ) : (
                              <Box
                                sx={{
                                  p: 1,
                                  minHeight: 40,
                                  display: 'flex',
                                  alignItems: 'center',
                                  wordBreak: 'break-word',
                                  whiteSpace: 'pre-wrap',
                                }}
                              >
                                {row[header] || ''}
                              </Box>
                            )}
                          </TableCell>
                        );
                      })}
                      <TableCell>
                        <Tooltip title="Delete Row">
                          <IconButton
                            size="small"
                            onClick={() => handleDeleteRow(rowIndex)}
                            color="error"
                          >
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Paper>
        </Box>
      </DialogContent>
    </Dialog>
  );
};

export default CSVSpreadsheetViewer;
