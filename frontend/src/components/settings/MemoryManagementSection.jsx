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
  ListItemButton,
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
import EditIcon from "@mui/icons-material/Edit";
import SettingsSection from "./SettingsSection";
import LessonSummaryModal from "../modals/LessonSummaryModal";

// Use the same VITE_API_BASE_URL pattern as other components (DirectoryPicker,
// ImageBatchWindow, TaskQueueIndicator, etc). Default to relative "/api" so the
// Vite dev server proxy (vite.config.js) routes to whichever port FLASK_PORT
// is actually bound to. The previous hardcoded fallback "http://localhost:5002/api"
// silently broke every memory save when the backend happened to land on :5000.
const BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

// Parse a lesson_summary memory's JSON content. Returns null if the content
// isn't valid JSON — caller falls back to generic rendering so malformed
// lessons are still visible (and editable via the plain text dialog).
const parseLesson = (memory) => {
  if (memory?.source !== "lesson_summary") return null;
  try {
    const parsed = JSON.parse(memory.content);
    if (!parsed || typeof parsed !== "object") return null;
    return {
      title: (parsed.title || "Untitled Lesson").toString(),
      stepCount: Array.isArray(parsed.steps) ? parsed.steps.length : 0,
      paramCount: Array.isArray(parsed.parameters) ? parsed.parameters.length : 0,
    };
  } catch {
    return null;
  }
};

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

  // Edit dialog state — two flavors: structured (lesson_summary) vs plain (everything else)
  const [editTarget, setEditTarget] = useState(null); // memory object
  const [lessonEdit, setLessonEdit] = useState(null); // { memoryId, title, steps }
  const [editContent, setEditContent] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);

  const openEditForMemory = (memory) => {
    if (memory?.source === "lesson_summary") {
      // Try to parse JSON content into {title, steps}. If malformed, fall
      // through to plain text edit so the user can fix it by hand.
      try {
        const parsed = JSON.parse(memory.content);
        setLessonEdit({
          memoryId: memory.id,
          title: parsed?.title || "Lesson",
          steps: Array.isArray(parsed?.steps) ? parsed.steps : [],
          parameters: Array.isArray(parsed?.parameters) ? parsed.parameters : [],
        });
        return;
      } catch {
        /* fall through to plain edit */
      }
    }
    setEditTarget(memory);
    setEditContent(memory?.content || "");
  };

  const handleSavePlainEdit = async () => {
    if (!editTarget?.id) return;
    const content = editContent.trim();
    if (!content) return;
    setSavingEdit(true);
    try {
      const res = await fetch(`${BASE_URL}/memory/${editTarget.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      const data = await res.json();
      if (data.success) {
        setMemories((prev) => prev.map((m) => (m.id === editTarget.id ? data.memory : m)));
        setEditTarget(null);
      } else {
        alert(data.error || "Failed to update memory");
      }
    } catch (err) {
      alert(err.message);
    } finally {
      setSavingEdit(false);
    }
  };

  const fetchMemories = async (query = "") => {
    setLoading(true);
    setError(null);
    try {
      const url = new URL(`${BASE_URL}/memory`, window.location.origin);
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
            {memories.map((memory, index) => {
              const lesson = parseLesson(memory);
              return (
                <React.Fragment key={memory.id}>
                  {index > 0 && <Divider />}
                  {lesson ? (
                    // Lesson card: whole row is a click target that opens the
                    // LessonSummaryModal. Mirrors the modal's header style so
                    // the identity of each lesson feels continuous between list
                    // and detail view.
                    <ListItem disablePadding secondaryAction={
                      <IconButton edge="end" aria-label="delete" onClick={(e) => { e.stopPropagation(); handleDelete(memory.id); }} color="error">
                        <CloseIcon />
                      </IconButton>
                    }>
                      <ListItemButton onClick={() => openEditForMemory(memory)} sx={{ py: 1.5 }}>
                        <ListItemText
                          disableTypography
                          primary={
                            <Typography
                              sx={{
                                fontWeight: 700,
                                letterSpacing: "0.04em",
                                textTransform: "uppercase",
                                fontSize: "0.95rem",
                              }}
                            >
                              {lesson.title}
                            </Typography>
                          }
                          secondary={
                            <Box sx={{ display: "flex", alignItems: "center", gap: 1, mt: 0.5, flexWrap: "wrap" }}>
                              <Typography variant="caption" color="text.secondary">
                                {lesson.stepCount} step{lesson.stepCount === 1 ? "" : "s"}
                                {lesson.paramCount > 0 && ` • ${lesson.paramCount} parameter${lesson.paramCount === 1 ? "" : "s"}`}
                                {" • "}{new Date(memory.created_at).toLocaleDateString()}
                              </Typography>
                              <Chip size="small" label="lesson" sx={{ height: 20, fontSize: "0.7rem" }} />
                              {memory.tags && memory.tags.map((tag, i) => (
                                <Chip key={i} size="small" label={tag} variant="outlined" sx={{ height: 20, fontSize: "0.7rem" }} />
                              ))}
                            </Box>
                          }
                        />
                      </ListItemButton>
                    </ListItem>
                  ) : (
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
                        <IconButton edge="end" aria-label="edit" onClick={() => openEditForMemory(memory)} sx={{ mr: 0.5 }}>
                          <EditIcon />
                        </IconButton>
                        <IconButton edge="end" aria-label="delete" onClick={() => handleDelete(memory.id)} color="error">
                          <CloseIcon />
                        </IconButton>
                      </ListItemSecondaryAction>
                    </ListItem>
                  )}
                </React.Fragment>
              );
            })}
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

      {/* Plain-text edit dialog for any non-lesson memory */}
      <Dialog
        open={!!editTarget}
        onClose={() => !savingEdit && setEditTarget(null)}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>Edit Memory</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label="Memory Content"
            fullWidth
            multiline
            rows={5}
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditTarget(null)} disabled={savingEdit}>Cancel</Button>
          <Button
            onClick={handleSavePlainEdit}
            variant="contained"
            disabled={savingEdit || !editContent.trim()}
          >
            {savingEdit ? <CircularProgress size={24} /> : "Save"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Structured step editor for lesson_summary memories */}
      <LessonSummaryModal
        open={!!lessonEdit}
        onClose={() => setLessonEdit(null)}
        memoryId={lessonEdit?.memoryId}
        initialTitle={lessonEdit?.title}
        initialSteps={lessonEdit?.steps}
        initialParameters={lessonEdit?.parameters}
        onSaved={(updated) => {
          setLessonEdit(null);
          if (updated?.id) {
            setMemories((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
          } else {
            fetchMemories(searchQuery);
          }
        }}
      />
    </SettingsSection>
  );
};

export default MemoryManagementSection;
