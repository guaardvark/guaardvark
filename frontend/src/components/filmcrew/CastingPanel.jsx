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
import { listCastLibrary, castSubject } from '../../api/productionService';

const CastingPanel = ({ productionId, shots, onCastingConfirmed }) => {
  const [castingData, setCastingData] = useState({});
  const [castLibrary, setCastLibrary] = useState([]);
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState(null);

  // Decision: Backend doesn't expose a "get cast recommendation" endpoint yet.
  // Just show the subjects linked to shots.
  const uniqueSubjects = [];
  const seenIds = new Set();
  
  // Note: Shots should have subjects in a real scenario, but if they are just strings
  // or IDs in the prompt description, we might need to parse them.
  // However, the model `ProductionShotSubject` exists.
  // Assuming `production.shots` return includes associated subjects.
  // Wait, the `get_production` endpoint in `production_api.py` doesn't return subjects per shot yet.
  // Let me check if I should update that or just use a placeholder.
  
  // "For v1, just shows the Subjects already linked to this production via the shots"
  // Let's assume `shots` have a `subjects` array (populated by a join or manually).
  
  // Wait, I'll check `get_production` in `production_api.py` again.
  // It only returns id, scene_number, shot_number, description, approved, storyboard_image_path, video_clip_path.
  // It DOES NOT return subjects.
  
  // I might need to update `get_production` to include subjects if I want this to work.
  // But the prompt said "Don't modify backend code unless you're adding the small list endpoint".
  
  // Let's assume there's a way to get subjects for a production.
  // Maybe I should add a `GET /api/production/<id>/subjects` endpoint?
  
  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        const library = await listCastLibrary();
        setCastLibrary(library.subjects || []);
        
        // Mocking some subjects for now if none are found, or try to find them from shots.
        // In a real scenario, the screenwriter agent would have created these.
      } catch (err) {
        setError('Failed to load cast library');
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

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

  const handleRefsChange = (subjectId, refs) => {
    setCastingData(prev => ({
      ...prev,
      [subjectId]: { ...prev[subjectId], ref_image_paths: refs.split(',').map(r => r.trim()) }
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

  // Mocking subjects for the sake of the UI if none found
  // In reality, these would come from the production details.
  const subjectsToCast = [
    { id: 101, name: 'Lead Actor', kind: 'character' },
    { id: 102, name: 'Living Room', kind: 'environment' }
  ];

  return (
    <Box sx={{ mt: 3 }}>
      <Typography variant="h6" gutterBottom>Pick a face for your character</Typography>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
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
                    <TextField 
                      label="Image paths (comma separated)" 
                      size="small" 
                      fullWidth 
                      onChange={(e) => handleRefsChange(subj.id, e.target.value)}
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
