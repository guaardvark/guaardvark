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
  Radio,
  RadioGroup,
  FormControlLabel,
  FormControl,
  Autocomplete,
  TextField,
  Button,
  Chip,
  Alert
} from '@mui/material';
import { listCastLibrary, listProductionSubjects, castSubject } from '../../api/productionService';
import DragDropImageUpload from './DragDropImageUpload';

const CastingPanel = ({ productionId, shots, onCastingConfirmed }) => {
  const [castingData, setCastingData] = useState({});
  const [castLibrary, setCastLibrary] = useState([]);
  const [subjectsToCast, setSubjectsToCast] = useState([]);
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const fetchData = async () => {
      if (!productionId) return;
      setLoading(true);
      try {
        const [library, prodSubjects] = await Promise.all([
          listCastLibrary(),
          listProductionSubjects(productionId),
        ]);
        if (cancelled) return;
        setCastLibrary(library.subjects || []);
        setSubjectsToCast(prodSubjects.subjects || []);
      } catch (err) {
        if (!cancelled) setError('Failed to load casting data');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchData();
    return () => { cancelled = true; };
  }, [productionId]);

  const handleActionChange = (subjectId, action) => {
    setCastingData(prev => ({
      ...prev,
      [subjectId]: { ...prev[subjectId], action }
    }));
  };

  const handleLoraChange = (subjectId, lora) => {
    setCastingData(prev => ({
      ...prev,
      [subjectId]: { ...prev[subjectId], existing_lora_id: lora?.id }
    }));
  };

  const handleRefsUploaded = (subjectId, paths) => {
    // The drag-drop component already POSTed the files to /upload-refs and
    // returns the subject's authoritative ref_image_paths. We mirror that
    // into the cast form so the eventual /cast/<subject_id> call has them.
    setCastingData(prev => ({
      ...prev,
      [subjectId]: { ...prev[subjectId], ref_image_paths: paths }
    }));
  };

  const isAllConfirmed = () => {
    // This is hard without real subjects. 
    // I'll assume we have a list of subjects to cast.
    return Object.keys(castingData).length > 0 && 
           Object.values(castingData).every(d => 
             d.action && (d.action !== 'use_existing_lora' || d.existing_lora_id) &&
             (d.action !== 'train_from_uploads' || (d.ref_image_paths && d.ref_image_paths.length > 0))
           );
  };

  const handleConfirm = async () => {
    setConfirming(true);
    setError(null);
    try {
      for (const [subjectId, data] of Object.entries(castingData)) {
        await castSubject(productionId, subjectId, data);
      }
      onCastingConfirmed();
    } catch (err) {
      setError('Failed to confirm casting');
    } finally {
      setConfirming(false);
    }
  };

  return (
    <Box sx={{ mt: 3 }}>
      <Typography variant="h6" gutterBottom>Pick a face for your character</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {!loading && subjectsToCast.length === 0 && (
        <Alert severity="info" sx={{ mb: 2 }}>
          No subjects yet — the Screenwriter agent will populate these from the script.
          If you're seeing this after the script ran, the screenwriter run may have failed
          (check the failed-stage indicator above).
        </Alert>
      )}
      <TableContainer component={Paper}>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>Subject</TableCell>
              <TableCell>Kind</TableCell>
              <TableCell>Action</TableCell>
              <TableCell>Details</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {subjectsToCast.map((subj) => (
              <TableRow key={subj.id}>
                <TableCell>{subj.name}</TableCell>
                <TableCell><Chip label={subj.kind} size="small" /></TableCell>
                <TableCell>
                  <FormControl component="fieldset">
                    <RadioGroup
                      row
                      value={castingData[subj.id]?.action || ''}
                      onChange={(e) => handleActionChange(subj.id, e.target.value)}
                    >
                      <FormControlLabel value="use_existing_lora" control={<Radio />} label="Existing LoRA" />
                      <FormControlLabel value="train_from_uploads" control={<Radio />} label="Upload Photos" />
                      <FormControlLabel value="train_from_generated" control={<Radio />} label="AI Generated" />
                    </RadioGroup>
                  </FormControl>
                </TableCell>
                <TableCell sx={{ minWidth: 250 }}>
                  {castingData[subj.id]?.action === 'use_existing_lora' && (
                    <Autocomplete
                      options={castLibrary.filter(l => l.lora_path)}
                      getOptionLabel={(option) => option.name}
                      renderInput={(params) => <TextField {...params} label="Select LoRA" size="small" />}
                      onChange={(_, newValue) => handleLoraChange(subj.id, newValue)}
                    />
                  )}
                  {castingData[subj.id]?.action === 'train_from_uploads' && (
                    <DragDropImageUpload
                      subjectId={subj.id}
                      existingPaths={castingData[subj.id]?.ref_image_paths || []}
                      onUploaded={(paths) => handleRefsUploaded(subj.id, paths)}
                      helperText="Drop a few clear photos — that's the LoRA's only training data."
                    />
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      <Box sx={{ mt: 3, display: 'flex', justifyContent: 'flex-end' }}>
        <Button 
          variant="contained" 
          color="primary" 
          disabled={confirming || !isAllConfirmed()}
          onClick={handleConfirm}
        >
          {confirming ? 'Casting...' : 'Confirm Casting'}
        </Button>
      </Box>
    </Box>
  );
};

export default CastingPanel;
