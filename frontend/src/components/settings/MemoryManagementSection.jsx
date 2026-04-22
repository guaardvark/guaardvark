import React, { useState, useEffect } from "react";
import {
  Box,
  Typography,
  Paper,
  Button,
  TextField,
  IconButton,
  List,
  ListItem,
  ListItemText,
  ListItemSecondaryAction,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  CircularProgress,
  Divider,
  Alert,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import AddIcon from "@mui/icons-material/Add";
import SearchIcon from "@mui/icons-material/Search";
import MemoryIcon from "@mui/icons-material/Memory";
import SettingsSection from "./SettingsSection";

const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:5002/api";

const MemoryManagementSection = () => {
  const [memories, setMemories] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  
  // Add memory dialog state
  const [openAdd, setOpenAdd] = useState(false);
  const [newContent, setNewContent] = useState("");
  const [newTags, setNewTags] = useState("");
  const [newType, setNewType] = useState("fact");
  const [adding, setAdding] = useState(false);

  const fetchMemories = async (query = "") => {
    setLoading(true);
    setError(null);
    try {
      const url = new URL(`${BASE_URL}/memory`);
      if (query) url.searchParams.append("search", query);
      url.searchParams.append("limit", 100);
      
      const res = await fetch(url);
      const data = await res.json();
      
      if (data.success) {
        setMemories(data.memories);
      } else {
        setError(data.error || "Failed to load memories");
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMemories();
  }, []);

  const handleSearch = (e) => {
    e.preventDefault();
    fetchMemories(searchQuery);
  };

  const handleDelete = async (id) => {
    if (!window.confirm("Are you sure you want to delete this memory?")) return;
    
    try {
      const res = await fetch(`${BASE_URL}/memory/${id}`, {
        method: "DELETE",
      });
      const data = await res.json();
      
      if (data.success) {
        setMemories(memories.filter((m) => m.id !== id));
      } else {
        alert(data.error || "Failed to delete memory");
      }
    } catch (err) {
      alert(err.message);
    }
  };

  const handleClearAll = async () => {
    if (!window.confirm("WARNING: Are you sure you want to delete ALL memories? This cannot be undone.")) return;
    
    try {
      const res = await fetch(`${BASE_URL}/memory/clear`, {
        method: "DELETE",
      });
      const data = await res.json();
      
      if (data.success) {
        setMemories([]);
      } else {
        alert(data.error || "Failed to clear memories");
      }
    } catch (err) {
      alert(err.message);
    }
  };

  const handleAddMemory = async () => {
    if (!newContent.trim()) return;
    setAdding(true);
    try {
      const tagsArray = newTags.split(",").map(t => t.trim()).filter(Boolean);
      const res = await fetch(`${BASE_URL}/memory`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: newContent,
          type: newType,
          tags: tagsArray,
          source: "manual"
        }),
      });
      const data = await res.json();
      
      if (data.success) {
        setOpenAdd(false);
        setNewContent("");
        setNewTags("");
        fetchMemories(searchQuery); // refresh list
      } else {
        alert(data.error || "Failed to add memory");
      }
    } catch (err) {
      alert(err.message);
    } finally {
      setAdding(false);
    }
  };

  return (
    <SettingsSection title="Agent Memory" icon={<MemoryIcon />}>
      <Typography variant="body2" color="text.secondary" paragraph>
        Manage the long-term memories, facts, and preferences the agent has learned about you. The agent uses these to personalize its responses.
      </Typography>

      <Box sx={{ display: "flex", gap: 2, mb: 3 }}>
        <form onSubmit={handleSearch} style={{ flexGrow: 1, display: "flex", gap: "8px" }}>
          <TextField
            size="small"
            fullWidth
            placeholder="Search memories..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            InputProps={{
              startAdornment: <SearchIcon color="action" sx={{ mr: 1 }} />,
            }}
          />
          <Button type="submit" variant="outlined">Search</Button>
        </form>
        <Button 
          variant="contained" 
          startIcon={<AddIcon />}
          onClick={() => setOpenAdd(true)}
        >
          Add Memory
        </Button>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>
      )}

      <Paper variant="outlined" sx={{ maxHeight: 400, overflow: "auto" }}>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
            <CircularProgress />
          </Box>
        ) : memories.length === 0 ? (
          <Box sx={{ textAlign: "center", p: 4, color: "text.secondary" }}>
            <Typography>No memories found.</Typography>
          </Box>
        ) : (
          <List disablePadding>
            {memories.map((memory, index) => (
              <React.Fragment key={memory.id}>
                {index > 0 && <Divider />}
                <ListItem>
                  <ListItemText
                    primary={memory.content}
                    secondary={
                      <Box sx={{ display: "flex", alignItems: "center", gap: 1, mt: 0.5, flexWrap: "wrap" }}>
                        <Typography variant="caption" color="text.secondary">
                          {new Date(memory.created_at).toLocaleDateString()} • Source: {memory.source}
                        </Typography>
                        <Chip size="small" label={memory.type} sx={{ height: 20, fontSize: "0.7rem" }} />
                        {memory.tags && memory.tags.map((tag, i) => (
                          <Chip key={i} size="small" label={tag} variant="outlined" sx={{ height: 20, fontSize: "0.7rem" }} />
                        ))}
                      </Box>
                    }
                  />
                  <ListItemSecondaryAction>
                    <IconButton edge="end" aria-label="delete" onClick={() => handleDelete(memory.id)} color="error">
                      <CloseIcon />
                    </IconButton>
                  </ListItemSecondaryAction>
                </ListItem>
              </React.Fragment>
            ))}
          </List>
        )}
      </Paper>

      {memories.length > 0 && (
        <Box sx={{ mt: 2, display: "flex", justifyContent: "flex-end" }}>
          <Button color="error" size="small" onClick={handleClearAll}>
            Clear All Memories
          </Button>
        </Box>
      )}

      {/* Add Memory Dialog */}
      <Dialog open={openAdd} onClose={() => !adding && setOpenAdd(false)} fullWidth maxWidth="sm">
        <DialogTitle>Add New Memory</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label="Memory Content"
            fullWidth
            multiline
            rows={3}
            value={newContent}
            onChange={(e) => setNewContent(e.target.value)}
            placeholder="e.g., I prefer Python code to be formatted with Black."
            sx={{ mb: 2, mt: 1 }}
          />
          <Box sx={{ display: "flex", gap: 2 }}>
            <TextField
              select
              label="Type"
              value={newType}
              onChange={(e) => setNewType(e.target.value)}
              SelectProps={{ native: true }}
              sx={{ minWidth: 120 }}
            >
              <option value="fact">Fact</option>
              <option value="preference">Preference</option>
              <option value="instruction">Instruction</option>
              <option value="note">Note</option>
            </TextField>
            <TextField
              label="Tags (comma separated)"
              fullWidth
              value={newTags}
              onChange={(e) => setNewTags(e.target.value)}
              placeholder="e.g., coding, python"
            />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpenAdd(false)} disabled={adding}>Cancel</Button>
          <Button onClick={handleAddMemory} variant="contained" disabled={adding || !newContent.trim()}>
            {adding ? <CircularProgress size={24} /> : "Save"}
          </Button>
        </DialogActions>
      </Dialog>
    </SettingsSection>
  );
};

export default MemoryManagementSection;
