import React, { useState, useEffect } from "react";
import {
  Box,
  Typography,
  Paper,
  Button,
  TextField,
  IconButton,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  CircularProgress,
  Alert,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import AddIcon from "@mui/icons-material/Add";
import SearchIcon from "@mui/icons-material/Search";
import MemoryIcon from "@mui/icons-material/Memory";
import SettingsSection from "./SettingsSection";
import LessonSummaryModal from "../modals/LessonSummaryModal";

// Compact spreadsheet-style timestamp: "MM/DD HH:MM:SS". Full ISO available
// on hover via title attribute for forensic detail.
const formatTimestamp = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${mm}/${dd} ${hh}:${mi}:${ss}`;
};

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

  // Type-aware placeholder. Preference reads naturally in first person (it's
  // *about* the user). Everything else gets imperative/observational phrasing
  // so the line still makes sense when reinjected under the
  // "User's saved memories" header in the system prompt.
  const placeholderByType = {
    fact: "e.g., The user's main machine has 64GB RAM and an RTX 4090.",
    preference: "e.g., I prefer Python code formatted with Black.",
    note: "e.g., After a click, the icon may be obscured by the app that opened — treat that as success.",
  };

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

      <TableContainer component={Paper} variant="outlined" sx={{ maxHeight: 480 }}>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
            <CircularProgress />
          </Box>
        ) : memories.length === 0 ? (
          <Box sx={{ textAlign: "center", p: 4, color: "text.secondary" }}>
            <Typography>No memories found.</Typography>
          </Box>
        ) : (
          // Spreadsheet-style memory table. Clicking any row opens the existing
          // edit modal (lesson_summary memories go to LessonSummaryModal,
          // everything else to the plain-text dialog) — that's the only way to
          // edit now that the pencil icon is gone. Delete (X) is the only inline action.
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell sx={{ fontWeight: 700, fontSize: "0.7rem", width: 130 }}>When</TableCell>
                <TableCell sx={{ fontWeight: 700, fontSize: "0.7rem" }}>Content</TableCell>
                <TableCell sx={{ fontWeight: 700, fontSize: "0.7rem", width: 100 }}>Type</TableCell>
                <TableCell sx={{ fontWeight: 700, fontSize: "0.7rem", width: 110 }}>Source</TableCell>
                <TableCell sx={{ fontWeight: 700, fontSize: "0.7rem" }}>Tags</TableCell>
                <TableCell sx={{ fontWeight: 700, fontSize: "0.7rem", width: 48 }} align="right"></TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {memories.map((memory) => {
                const lesson = parseLesson(memory);
                const contentDisplay = lesson
                  ? `${lesson.title} (${lesson.stepCount} step${lesson.stepCount === 1 ? "" : "s"}${lesson.paramCount > 0 ? `, ${lesson.paramCount} param${lesson.paramCount === 1 ? "" : "s"}` : ""})`
                  : memory.content;
                const typeLabel = lesson ? "lesson" : (memory.type || "—");
                return (
                  <TableRow
                    key={memory.id}
                    hover
                    onClick={() => openEditForMemory(memory)}
                    sx={{ cursor: "pointer", "& td": { py: 0.5, fontSize: "0.75rem" } }}
                  >
                    <TableCell sx={{ fontFamily: "monospace", whiteSpace: "nowrap" }}>
                      <Tooltip title={new Date(memory.created_at).toLocaleString()} placement="top" arrow>
                        <span>{formatTimestamp(memory.created_at)}</span>
                      </Tooltip>
                    </TableCell>
                    <TableCell
                      sx={{
                        maxWidth: 360,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        fontWeight: lesson ? 600 : 400,
                      }}
                    >
                      <Tooltip title={memory.content} placement="top-start" arrow>
                        <span>{contentDisplay}</span>
                      </Tooltip>
                    </TableCell>
                    <TableCell>
                      <Chip size="small" label={typeLabel} sx={{ height: 18, fontSize: "0.65rem" }} />
                    </TableCell>
                    <TableCell sx={{ color: "text.secondary" }}>{memory.source || "—"}</TableCell>
                    <TableCell sx={{ maxWidth: 220, overflow: "hidden" }}>
                      <Box sx={{ display: "flex", gap: 0.5, flexWrap: "nowrap", overflow: "hidden" }}>
                        {(memory.tags || []).map((tag, i) => (
                          <Chip
                            key={i}
                            size="small"
                            label={tag}
                            variant="outlined"
                            sx={{ height: 18, fontSize: "0.6rem" }}
                          />
                        ))}
                      </Box>
                    </TableCell>
                    <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                      <IconButton
                        size="small"
                        aria-label="delete"
                        onClick={() => handleDelete(memory.id)}
                        color="error"
                      >
                        <CloseIcon fontSize="small" />
                      </IconButton>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </TableContainer>

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
            placeholder={placeholderByType[newType] || placeholderByType.fact}
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
          <Button
            size="small"
            color="error"
            disabled={savingEdit}
            sx={{ mr: "auto" }}
            onClick={async () => {
              const id = editTarget?.id;
              if (!id) return;
              setEditTarget(null);
              await handleDelete(id);
            }}
          >
            Delete
          </Button>
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
        onDelete={async () => {
          const id = lessonEdit?.memoryId;
          if (!id) return;
          setLessonEdit(null);
          await handleDelete(id);
        }}
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
