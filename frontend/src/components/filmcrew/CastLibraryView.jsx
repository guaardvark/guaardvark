import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Button,
  Chip,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
  CircularProgress,
  Alert
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import AddIcon from '@mui/icons-material/Add';
import { listCastLibrary, createCastSubject, deleteCastSubject } from '../../api/productionService';

const KINDS = ['character', 'environment', 'prop'];

const CastLibraryView = () => {
  const [subjects, setSubjects] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: '', kind: 'character', description: '', ref_image_paths: '' });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    loadLibrary();
  }, []);

  const loadLibrary = async () => {
    setLoading(true);
    try {
      const data = await listCastLibrary();
      setSubjects(data.subjects || []);
    } catch (err) {
      setError('Failed to load cast library');
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    setSubmitting(true);
    try {
      const payload = {
        ...form,
        ref_image_paths: form.ref_image_paths.split(',').map(s => s.trim()).filter(Boolean)
      };
      await createCastSubject(payload);
      setOpen(false);
      setForm({ name: '', kind: 'character', description: '', ref_image_paths: '' });
      loadLibrary();
    } catch (err) {
      setError('Failed to create subject');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id) => {
    if (window.confirm('Are you sure you want to delete this subject?')) {
      try {
        await deleteCastSubject(id);
        loadLibrary();
      } catch (err) {
        setError('Failed to delete subject');
      }
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 3 }}>
        <Typography variant="h5">Cast Library</Typography>
        <Button 
          variant="contained" 
          startIcon={<AddIcon />}
          onClick={() => setOpen(true)}
        >
          New Subject
        </Button>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 5 }}>
          <CircularProgress />
        </Box>
      ) : (
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Kind</TableCell>
                <TableCell>Description</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {subjects.map((s) => (
                <TableRow key={s.id}>
                  <TableCell sx={{ fontWeight: 'bold' }}>{s.name}</TableCell>
                  <TableCell>
                    <Chip label={s.kind} size="small" variant="outlined" color={s.kind === 'character' ? 'primary' : 'secondary'} />
                  </TableCell>
                  <TableCell>{s.description}</TableCell>
                  <TableCell>
                    <Chip 
                      label={s.training_status} 
                      size="small" 
                      color={s.training_status === 'trained' ? 'success' : 'default'} 
                    />
                  </TableCell>
                  <TableCell>
                    <IconButton onClick={() => handleDelete(s.id)} color="inherit">
                      <DeleteIcon />
                    </IconButton>
                  </TableCell>
                </TableRow>
              ))}
              {subjects.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} align="center">No subjects in the library yet.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Add to Cast Library</DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
            <TextField 
              label="Name" 
              fullWidth 
              value={form.name} 
              onChange={e => setForm({...form, name: e.target.value})}
            />
            <TextField
              select
              label="Kind"
              value={form.kind}
              onChange={e => setForm({...form, kind: e.target.value})}
              fullWidth
            >
              {KINDS.map((option) => (
                <MenuItem key={option} value={option}>
                  {option}
                </MenuItem>
              ))}
            </TextField>
            <TextField 
              label="Description" 
              fullWidth 
              multiline 
              rows={2}
              value={form.description} 
              onChange={e => setForm({...form, description: e.target.value})}
            />
            <TextField 
              label="Reference Image Paths (comma separated)" 
              fullWidth 
              value={form.ref_image_paths} 
              onChange={e => setForm({...form, ref_image_paths: e.target.value})}
            />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button onClick={handleCreate} variant="contained" disabled={submitting || !form.name}>
            {submitting ? 'Saving...' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default CastLibraryView;
