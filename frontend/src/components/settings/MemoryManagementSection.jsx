import React, { useState, useEffect, useRef } from "react";
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
import BlockIcon from "@mui/icons-material/Block";
import ArchiveOutlinedIcon from "@mui/icons-material/ArchiveOutlined";
import AddIcon from "@mui/icons-material/Add";
import SearchIcon from "@mui/icons-material/Search";
import MemoryIcon from "@mui/icons-material/Memory";
import SettingsSection from "./SettingsSection";
import LessonSummaryModal from "../modals/LessonSummaryModal";
import { ActionButton, ChoiceChips, ConfirmActionDialog, Hint } from "./ui";
import EntityContextMenu from "../common/EntityContextMenu";
import useContextMenu from "../../hooks/useContextMenu";
import copyText from "../../utils/copyText";
import { handleResponse } from "../../api/apiClient";
import { formatUiError } from "../../utils/uiError";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

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

// A memory API call's JSON body. An error status (or an offline backend)
// throws with the server's reason through apiClient.handleResponse instead
// of being read as a body without `success`.
const requestJson = async (url, init) => {
  const res = await fetch(url, init);
  if (res.ok === false) return handleResponse(res, { quiet: true });
  return res.json();
};

// Rows per request. The API answers one page and the real count of matching
// rows (total); the footer shows that count with a Load more button while
// rows remain.
const PAGE_SIZE = 100;

// Parse a lesson_summary memory's JSON content. Returns null if the content
// isn't valid JSON — caller falls back to generic rendering so malformed
// lessons are still visible (and editable via the plain text dialog).
const parseLesson = (memory) => {
  if (memory?.source !== "lesson_summary") return null;
  try {
    const parsed = JSON.parse(memory.content);
    if (!parsed || typeof parsed !== "object") return null;
    return {
      title: (parsed.title || translate("settingsMemory.untitledLesson")).toString(),
      stepCount: Array.isArray(parsed.steps) ? parsed.steps.length : 0,
      paramCount: Array.isArray(parsed.parameters) ? parsed.parameters.length : 0,
    };
  } catch {
    return null;
  }
};

// Words too common to tell two memories apart.
const COMMON_WORDS = new Set([
  "the", "and", "for", "with", "that", "this", "are", "was", "were", "has", "have", "had",
  "not", "but", "you", "your", "user", "users", "from", "into", "when", "then", "than",
  "its", "his", "her", "their", "they", "them", "our", "use", "uses", "used", "should",
]);

// Share of the shorter memory's words the other must hold, and the fewest
// shared words, before the two count as saying the same thing. Two words
// minimum keeps one common term ("docker") from pairing unrelated memories.
const SIMILAR_SHARE = 0.75;
const SIMILAR_MIN_SHARED = 2;

const memoryWords = (text) =>
  new Set(
    ((text || "").toLowerCase().match(/[a-z0-9]+/g) || []).filter((w) => w.length > 2 && !COMMON_WORDS.has(w)),
  );

/**
 * Loaded memories that read like `memory`, the ones Merge Duplicate offers.
 * Lesson summaries hold JSON steps, so they are never merged into.
 */
const similarMemories = (memory, memories) => {
  const words = memoryWords(memory?.content);
  if (!memory || words.size === 0) return [];
  return memories.filter((m) => {
    if (String(m.id) === String(memory.id) || m.source === "lesson_summary") return false;
    const other = memoryWords(m.content);
    let shared = 0;
    for (const w of words) if (other.has(w)) shared += 1;
    const smaller = Math.min(words.size, other.size);
    if (smaller === 0) return false;
    // The same words (a one-word memory and its copy too), or most of them.
    if (shared === words.size && shared === other.size) return true;
    return shared >= SIMILAR_MIN_SHARED && shared / smaller >= SIMILAR_SHARE;
  });
};

// `title`/`icon` default to the section heading the Settings page expects.
// AgentMemoryPage renders this under a PageLayout header that already says
// "Agent Memory", and passes title={null} so the name isn't printed twice.
const MemoryManagementSection = ({ title = translate("settingsMemory.title"), icon = <MemoryIcon /> }) => {
  const { t } = useUiTranslation();
  const [memories, setMemories] = useState([]);
  // The API's count of matching rows; null until the first page answers.
  const [total, setTotal] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);
  // The search, filters and sort the list on screen was fetched with. Load
  // more reuses these, not the controls' current values, which only Apply
  // Filters or Search put into effect.
  const appliedRef = useRef(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [filterType, setFilterType] = useState("");
  const [filterSource, setFilterSource] = useState("");
  const [filterStatus, setFilterStatus] = useState("active");
  const [sortMode, setSortMode] = useState("rank");
  
  // Add memory dialog state
  const [openAdd, setOpenAdd] = useState(false);
  const [newContent, setNewContent] = useState("");
  const [newTags, setNewTags] = useState("");
  const [newType, setNewType] = useState("fact");
  const [adding, setAdding] = useState(false);
  // Set when Add found its text already saved: the API answers with that
  // memory instead of writing a second copy.
  const [notice, setNotice] = useState(null);
  // A failed edit, delete, merge, clear or add; said above the table.
  const [actionError, setActionError] = useState(null);
  // Deletes wait for ConfirmActionDialog: one memory's id, or "all".
  const [pendingDelete, setPendingDelete] = useState(null);

  // Type-aware placeholder. Preference reads naturally in first person (it's
  // *about* the user). Everything else gets imperative/observational phrasing
  // so the line still makes sense when reinjected under the
  // "User's saved memories" header in the system prompt.
  const placeholderByType = {
    fact: t("settingsMemory.placeholders.fact"),
    preference: t("settingsMemory.placeholders.preference"),
    note: t("settingsMemory.placeholders.note"),
  };

  // Edit dialog state — two flavors: structured (lesson_summary) vs plain (everything else)
  const [editTarget, setEditTarget] = useState(null); // memory object
  const [lessonEdit, setLessonEdit] = useState(null); // { memoryId, title, steps }
  const [editContent, setEditContent] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);
  const [mergePickerOpen, setMergePickerOpen] = useState(false);
  const [mergeTargetId, setMergeTargetId] = useState("");
  const [mergeConfirmOpen, setMergeConfirmOpen] = useState(false);
  const [merging, setMerging] = useState(false);
  const rowMenu = useContextMenu();

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
      const data = await requestJson(`${BASE_URL}/memory/${editTarget.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (data.success) {
        setMemories((prev) => prev.map((m) => (m.id === editTarget.id ? data.memory : m)));
        setEditTarget(null);
      } else {
        setActionError(formatUiError(data.error || t("settingsMemory.errors.update")) || t("settingsMemory.errors.requestFailed"));
      }
    } catch (err) {
      setActionError(formatUiError(err.message) || t("settingsMemory.errors.requestFailed"));
    } finally {
      setSavingEdit(false);
    }
  };

  const memoryUrl = (applied, limit) => {
    const url = new URL(`${BASE_URL}/memory`, window.location.origin);
    if (applied.search) url.searchParams.append("search", applied.search);
    if (applied.type) url.searchParams.append("type", applied.type);
    if (applied.source) url.searchParams.append("source", applied.source);
    // "all" asks for every status; without it the API leaves out rows
    // marked wrong.
    if (applied.status) url.searchParams.append("status", applied.status);
    if (applied.sort) url.searchParams.append("sort", applied.sort);
    url.searchParams.append("limit", limit);
    return url;
  };

  // Always from the top, `rows` rows at most (rounded up to whole pages).
  // Fetching the whole shown range again, rather than the next offset, is
  // what keeps a page from skipping a row: an Archive or Wrong click keeps
  // its row on screen while the server's status filter no longer returns
  // it, so an offset of the on-screen count would start one row late.
  const loadRows = async (applied, rows, { quiet = false } = {}) => {
    if (quiet) setLoadingMore(true);
    else setLoading(true);
    setError(null);
    try {
      const limit = Math.max(PAGE_SIZE, Math.ceil(rows / PAGE_SIZE) * PAGE_SIZE);
      const data = await requestJson(memoryUrl(applied, limit));

      if (data.success) {
        appliedRef.current = applied;
        setMemories(data.memories);
        setTotal(typeof data.total === "number" ? data.total : data.memories.length);
      } else {
        setError(data.error || t("settingsMemory.errors.load"));
      }
    } catch (err) {
      setError(err.message);
    } finally {
      if (quiet) setLoadingMore(false);
      else setLoading(false);
    }
  };

  // Put the controls' values into effect (Search, Apply Filters, mount), and
  // reload: `keep` rows at least, so a save after Load more does not drop the
  // rows the person scrolled to.
  const fetchMemories = (query = "", keep = 0) =>
    loadRows(
      { search: query, type: filterType, source: filterSource, status: filterStatus, sort: sortMode },
      keep,
    );

  const applied = () =>
    appliedRef.current || { search: "", type: "", source: "", status: filterStatus, sort: sortMode };

  // One page more than is on screen, with what the list was fetched with.
  const loadMore = () => loadRows(applied(), memories.length + PAGE_SIZE, { quiet: true });

  // After a save: the shown range again (`extra` rows more), same query, so
  // the total is current and a new row has a place to appear.
  const reload = (extra = 0) => loadRows(applied(), memories.length + extra);

  useEffect(() => {
    fetchMemories();
  }, []);

  useEffect(() => {
    if (editTarget) return;
    setMergePickerOpen(false);
    setMergeConfirmOpen(false);
    setMergeTargetId("");
  }, [editTarget]);

  const handleSearch = (e) => {
    e.preventDefault();
    fetchMemories(searchQuery);
  };

  const handleDelete = (id) => setPendingDelete(id);

  const deleteMemory = async (id) => {
    setActionError(null);
    try {
      const data = await requestJson(`${BASE_URL}/memory/${id}`, {
        method: "DELETE",
      });
      
      if (data.success) {
        setMemories(memories.filter((m) => m.id !== id));
        setTotal((prev) => (prev == null ? prev : Math.max(0, prev - 1)));
      } else {
        setActionError(formatUiError(data.error || t("settingsMemory.errors.delete")) || t("settingsMemory.errors.requestFailed"));
      }
    } catch (err) {
      setActionError(formatUiError(err.message) || t("settingsMemory.errors.requestFailed"));
    }
  };

  const handleStatusChange = async (memory, status) => {
    try {
      const data = await requestJson(`${BASE_URL}/memory/${memory.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      if (data.success) {
        setMemories((prev) => prev.map((m) => (m.id === memory.id ? data.memory : m)));
      } else {
        setActionError(formatUiError(data.error || t("settingsMemory.errors.updateStatus")) || t("settingsMemory.errors.requestFailed"));
      }
    } catch (err) {
      setActionError(formatUiError(err.message) || t("settingsMemory.errors.requestFailed"));
    }
  };

  const handleMergeDuplicate = () => {
    if (!editTarget?.id) return;
    setMergeTargetId("");
    setMergePickerOpen(true);
  };

  // Merge is offered only when another loaded memory reads like this one.
  const mergeCandidates = editTarget ? similarMemories(editTarget, memories) : [];
  const mergeTarget = memories.find((m) => String(m.id) === String(mergeTargetId));

  const confirmMerge = async () => {
    if (!editTarget?.id || !mergeTarget) return;
    setMerging(true);
    const mergedContent = `${mergeTarget.content}\n\nMerged duplicate ${editTarget.id}: ${editContent.trim()}`;
    try {
      const updateData = await requestJson(`${BASE_URL}/memory/${mergeTarget.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: mergedContent }),
      });
      if (!updateData.success) {
        setActionError(formatUiError(updateData.error || t("settingsMemory.errors.merge")) || t("settingsMemory.errors.requestFailed"));
        return;
      }
      await handleStatusChange(editTarget, "archived");
      setMemories((prev) => prev.map((m) => (m.id === mergeTarget.id ? updateData.memory : m)));
      setEditTarget(null);
      setMergeConfirmOpen(false);
      setMergePickerOpen(false);
      setMergeTargetId("");
    } catch (err) {
      setActionError(formatUiError(err.message) || t("settingsMemory.errors.requestFailed"));
    } finally {
      setMerging(false);
    }
  };

  const handleClearAll = () => setPendingDelete("all");

  const clearAllMemories = async () => {
    setActionError(null);
    try {
      const data = await requestJson(`${BASE_URL}/memory/clear`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation: "CLEAR_MEMORIES" }),
      });
      
      if (data.success) {
        setMemories([]);
        setTotal(0);
      } else {
        setActionError(formatUiError(data.error || t("settingsMemory.errors.clear")) || t("settingsMemory.errors.requestFailed"));
      }
    } catch (err) {
      setActionError(formatUiError(err.message) || t("settingsMemory.errors.requestFailed"));
    }
  };

  const handleAddMemory = async () => {
    if (!newContent.trim()) return;
    setAdding(true);
    setNotice(null);
    try {
      const tagsArray = newTags.split(",").map(t => t.trim()).filter(Boolean);
      const data = await requestJson(`${BASE_URL}/memory`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: newContent,
          type: newType,
          tags: tagsArray,
          source: "manual"
        }),
      });

      if (data.success && data.already_saved) {
        const saved = data.memory || {};
        const text = saved.content || "";
        const quoted = text.length > 120 ? `${text.slice(0, 120)}…` : text;
        setOpenAdd(false);
        setNewContent("");
        setNewTags("");
        setNotice(
          t("settingsMemory.alreadySaved", {
            text: quoted,
            type: saved.type || t("settingsMemory.memoryNoun"),
            time: formatTimestamp(saved.created_at),
          }),
        );
        reload();
      } else if (data.success) {
        setOpenAdd(false);
        setNewContent("");
        setNewTags("");
        reload(1); // refresh list and total
      } else {
        setActionError(formatUiError(data.error || t("settingsMemory.errors.add")) || t("settingsMemory.errors.requestFailed"));
      }
    } catch (err) {
      setActionError(formatUiError(err.message) || t("settingsMemory.errors.requestFailed"));
    } finally {
      setAdding(false);
    }
  };

  return (
    <SettingsSection title={title} icon={icon}>
      <Typography variant="body2" color="text.secondary" paragraph>
        {t("settingsMemory.intro")}
      </Typography>

      <Box sx={{ display: "flex", gap: 2, mb: 3 }}>
        <form onSubmit={handleSearch} style={{ flexGrow: 1, display: "flex", gap: "8px" }}>
          <TextField
            size="small"
            fullWidth
            placeholder={t("settingsMemory.searchPlaceholder")}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            InputProps={{
              startAdornment: <SearchIcon color="action" sx={{ mr: 1 }} />,
            }}
          />
          <Button type="submit" variant="outlined">{t("settingsMemory.search")}</Button>
        </form>
        <Button 
          variant="contained" 
          startIcon={<AddIcon />}
          onClick={() => setOpenAdd(true)}
        >
          {t("settingsMemory.addMemory")}
        </Button>
      </Box>

      <Box sx={{ display: "flex", gap: 1.5, mb: 2, flexWrap: "wrap" }}>
        <TextField select size="small" label={t("settingsMemory.filters.type")} value={filterType} onChange={(e) => setFilterType(e.target.value)} SelectProps={{ native: true }} InputLabelProps={{ shrink: true }} sx={{ minWidth: 130 }}>
          <option value="">{t("settingsMemory.filters.allTypes")}</option>
          <option value="fact">{t("settingsMemory.filters.fact")}</option>
          <option value="preference">{t("settingsMemory.filters.preference")}</option>
          <option value="note">{t("settingsMemory.filters.note")}</option>
          <option value="lesson">{t("settingsMemory.filters.lesson")}</option>
          <option value="belief_update">{t("settingsMemory.filters.beliefUpdate")}</option>
          <option value="snippet">{t("settingsMemory.filters.snippet")}</option>
        </TextField>
        <TextField select size="small" label={t("settingsMemory.filters.source")} value={filterSource} onChange={(e) => setFilterSource(e.target.value)} SelectProps={{ native: true }} InputLabelProps={{ shrink: true }} sx={{ minWidth: 150 }}>
          <option value="">{t("settingsMemory.filters.allSources")}</option>
          <option value="manual">{t("settingsMemory.filters.manual")}</option>
          <option value="chat">{t("settingsMemory.filters.chat")}</option>
          <option value="cli">{t("settingsMemory.filters.cli")}</option>
          <option value="agent">{t("settingsMemory.filters.agent")}</option>
          <option value="lesson_summary">{t("settingsMemory.filters.lessonSummary")}</option>
          <option value="learned_from_feedback">{t("settingsMemory.filters.feedback")}</option>
          <option value="candidate_recipe">{t("settingsMemory.filters.candidateRecipe")}</option>
        </TextField>
        <TextField select size="small" label={t("settingsMemory.filters.status")} value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)} SelectProps={{ native: true }} sx={{ minWidth: 130 }}>
          <option value="all">{t("settingsMemory.filters.allStatus")}</option>
          <option value="active">{t("settingsMemory.filters.active")}</option>
          <option value="archived">{t("settingsMemory.filters.archived")}</option>
          <option value="wrong">{t("settingsMemory.filters.wrong")}</option>
        </TextField>
        <TextField select size="small" label={t("settingsMemory.filters.sort")} value={sortMode} onChange={(e) => setSortMode(e.target.value)} SelectProps={{ native: true }} sx={{ minWidth: 130 }}>
          <option value="rank">{t("settingsMemory.filters.rank")}</option>
          <option value="">{t("settingsMemory.filters.newest")}</option>
        </TextField>
        <Button size="small" variant="outlined" onClick={() => fetchMemories(searchQuery)}>
          {t("settingsMemory.filters.apply")}
        </Button>
      </Box>

      {error && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          action={<Button color="inherit" size="small" onClick={() => reload()}>{t("settingsMemory.retry")}</Button>}
        >
          {error}
        </Alert>
      )}
      {notice && (
        <Alert severity="info" sx={{ mb: 2 }} onClose={() => setNotice(null)}>{notice}</Alert>
      )}
      {actionError && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setActionError(null)}>{actionError}</Alert>
      )}

      <TableContainer component={Paper} variant="outlined" sx={{ maxHeight: 480 }}>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
            <CircularProgress />
          </Box>
        ) : memories.length === 0 ? (
          <Box sx={{ textAlign: "center", p: 4, color: "text.secondary" }}>
            <Typography>{error ? t("settingsMemory.loadFailed") : t("settingsMemory.empty")}</Typography>
          </Box>
        ) : (
          // Spreadsheet-style memory table. Clicking any row opens the existing
          // edit modal (lesson_summary memories go to LessonSummaryModal,
          // everything else to the plain-text dialog) — that's the only way to
          // edit now that the pencil icon is gone. Delete (X) is the only inline action.
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell sx={{ fontWeight: 700, fontSize: "0.7rem", width: 130 }}>{t("settingsMemory.columns.when")}</TableCell>
                <TableCell sx={{ fontWeight: 700, fontSize: "0.7rem" }}>{t("settingsMemory.columns.content")}</TableCell>
                <TableCell sx={{ fontWeight: 700, fontSize: "0.7rem", width: 100 }}>{t("settingsMemory.columns.type")}</TableCell>
                <TableCell sx={{ fontWeight: 700, fontSize: "0.7rem", width: 110 }}>{t("settingsMemory.columns.source")}</TableCell>
                <TableCell sx={{ fontWeight: 700, fontSize: "0.7rem", width: 90 }}>{t("settingsMemory.columns.rank")}</TableCell>
                <TableCell sx={{ fontWeight: 700, fontSize: "0.7rem", width: 100 }}>{t("settingsMemory.columns.status")}</TableCell>
                <TableCell sx={{ fontWeight: 700, fontSize: "0.7rem" }}>{t("settingsMemory.columns.tags")}</TableCell>
                <TableCell sx={{ fontWeight: 700, fontSize: "0.7rem", width: 120, whiteSpace: "nowrap" }} align="right"></TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {memories.map((memory) => {
                const lesson = parseLesson(memory);
                const contentDisplay = lesson
                  ? lesson.paramCount > 0
                    ? t("settingsMemory.lessonSummaryWithParams", {
                        title: lesson.title,
                        steps: t("settingsMemory.lessonSteps", { count: lesson.stepCount }),
                        params: t("settingsMemory.lessonParams", { count: lesson.paramCount }),
                      })
                    : t("settingsMemory.lessonSummary", {
                        title: lesson.title,
                        steps: t("settingsMemory.lessonSteps", { count: lesson.stepCount }),
                      })
                  : memory.content;
                const typeLabel = lesson ? "lesson" : (memory.type || "—");
                return (
                  <TableRow
                    key={memory.id}
                    hover
                    onClick={() => openEditForMemory(memory)}
                    onContextMenu={(e) => rowMenu.open(e, memory)}
                    sx={{ cursor: "pointer", "& td": { py: 0.5, fontSize: "0.75rem" } }}
                  >
                    <TableCell sx={{ fontFamily: "monospace", whiteSpace: "nowrap" }}>
                      <Tooltip title={new Date(memory.created_at).toLocaleString(uiLocale())} placement="top" arrow>
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
                    <TableCell sx={{ color: "text.secondary" }}>
                      <Tooltip title={memory.rank_reason || t("settingsMemory.rankTooltip")} placement="top" arrow>
                        <span>{memory.rank_score ?? "—"}</span>
                      </Tooltip>
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={memory.status || "active"}
                        color={memory.status === "wrong" ? "error" : memory.status === "archived" ? "default" : "success"}
                        variant={memory.status === "active" ? "filled" : "outlined"}
                        sx={{ height: 18, fontSize: "0.6rem" }}
                      />
                    </TableCell>
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
                    <TableCell align="right" sx={{ whiteSpace: "nowrap" }} onClick={(e) => e.stopPropagation()}>
                      {memory.status !== "active" && (
                        <Button size="small" color="success" onClick={() => handleStatusChange(memory, "active")}>
                          {t("settingsMemory.restore")}
                        </Button>
                      )}
                      <Tooltip title={memory.status === "archived" ? t("settingsMemory.archivedTooltip") : t("settingsMemory.archive")}>
                        <span>
                          <IconButton
                            size="small"
                            aria-label={t("settingsMemory.archiveAria")}
                            onClick={() => handleStatusChange(memory, "archived")}
                            disabled={memory.status === "archived"}
                          >
                            <ArchiveOutlinedIcon fontSize="small" />
                          </IconButton>
                        </span>
                      </Tooltip>
                      <Tooltip title={memory.status === "wrong" ? t("settingsMemory.markedWrong") : t("settingsMemory.markWrong")}>
                        <span>
                          <IconButton
                            size="small"
                            color="warning"
                            aria-label={t("settingsMemory.markWrongAria")}
                            onClick={() => handleStatusChange(memory, "wrong")}
                            disabled={memory.status === "wrong"}
                          >
                            <BlockIcon fontSize="small" />
                          </IconButton>
                        </span>
                      </Tooltip>
                      <IconButton
                        size="small"
                        aria-label={t("settingsMemory.deleteAria")}
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

      <EntityContextMenu
        anchorPosition={rowMenu.anchorPosition}
        onClose={rowMenu.close}
        actions={
          rowMenu.payload
            ? [
                { label: t("settingsMemory.menu.edit"), onClick: () => openEditForMemory(rowMenu.payload) },
                { label: t("settingsMemory.menu.copy"), onClick: () => copyText(rowMenu.payload.content) },
                rowMenu.payload.status !== "active" && {
                  label: t("settingsMemory.menu.restore"),
                  onClick: () => handleStatusChange(rowMenu.payload, "active"),
                  dividerBefore: true,
                },
                {
                  label: t("settingsMemory.menu.archive"),
                  onClick: () => handleStatusChange(rowMenu.payload, "archived"),
                  disabled: rowMenu.payload.status === "archived",
                  dividerBefore: rowMenu.payload.status === "active",
                },
                {
                  label: t("settingsMemory.menu.markWrong"),
                  onClick: () => handleStatusChange(rowMenu.payload, "wrong"),
                  disabled: rowMenu.payload.status === "wrong",
                },
                {
                  label: t("settingsMemory.menu.delete"),
                  onClick: () => handleDelete(rowMenu.payload.id),
                  color: "error.main",
                  dividerBefore: true,
                },
              ]
            : []
        }
      />

      {memories.length > 0 && (
        <Box sx={{ mt: 2, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <Typography variant="caption" color="text.secondary">
            {t("settingsMemory.total", { count: total ?? memories.length })}
            {total != null && memories.length < total && (
              <>
                {" "}
                {t("settingsMemory.showing", { count: memories.length })}
                <Button
                  size="small"
                  variant="text"
                  onClick={loadMore}
                  disabled={loadingMore}
                  sx={{ ml: 1, py: 0, minWidth: 0, textTransform: "none" }}
                >
                  {loadingMore ? t("settingsMemory.loading") : t("settingsMemory.loadMore")}
                </Button>
              </>
            )}
          </Typography>
          <Button color="error" size="small" onClick={handleClearAll}>
            {t("settingsMemory.clearAll")}
          </Button>
        </Box>
      )}

      {/* Add Memory Dialog */}
      <Dialog open={openAdd} onClose={() => !adding && setOpenAdd(false)} fullWidth maxWidth="sm">
        <DialogTitle>{t("settingsMemory.add.title")}</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label={t("settingsMemory.add.content")}
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
              label={t("settingsMemory.add.type")}
              value={newType}
              onChange={(e) => setNewType(e.target.value)}
              SelectProps={{ native: true }}
              sx={{ minWidth: 120 }}
            >
              <option value="fact">{t("settingsMemory.filters.fact")}</option>
              <option value="preference">{t("settingsMemory.filters.preference")}</option>
              <option value="note">{t("settingsMemory.filters.note")}</option>
            </TextField>
            <TextField
              label={t("settingsMemory.add.tags")}
              fullWidth
              value={newTags}
              onChange={(e) => setNewTags(e.target.value)}
              placeholder={t("settingsMemory.add.tagsPlaceholder")}
            />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpenAdd(false)} disabled={adding}>{t("settingsMemory.add.cancel")}</Button>
          <Button onClick={handleAddMemory} variant="contained" disabled={adding || !newContent.trim()}>
            {adding ? <CircularProgress size={24} /> : t("settingsMemory.add.save")}
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
        <DialogTitle>{t("settingsMemory.edit.title")}</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label={t("settingsMemory.edit.content")}
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
            {t("settingsMemory.edit.delete")}
          </Button>
          {mergeCandidates.length > 0 && (
            <ActionButton onClick={handleMergeDuplicate} disabled={savingEdit}>
              {t("settingsMemory.edit.mergeDuplicate")}
            </ActionButton>
          )}
          <Button onClick={() => setEditTarget(null)} disabled={savingEdit}>{t("settingsMemory.edit.cancel")}</Button>
          <Button
            onClick={handleSavePlainEdit}
            variant="contained"
            disabled={savingEdit || !editContent.trim()}
          >
            {savingEdit ? <CircularProgress size={24} /> : t("settingsMemory.edit.save")}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={mergePickerOpen}
        onClose={() => !merging && setMergePickerOpen(false)}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>{t("settingsMemory.mergePicker.title")}</DialogTitle>
        <DialogContent>
          <Hint sx={{ display: "block", mb: 1.5 }}>
            {t("settingsMemory.mergePicker.hint")}
          </Hint>
          {mergeCandidates.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              {t("settingsMemory.mergePicker.none")}
            </Typography>
          ) : (
            <Box sx={{ maxHeight: 280, overflow: "auto", py: 0.5 }}>
              <ChoiceChips
                ariaLabel={t("settingsMemory.mergePicker.aria")}
                value={mergeTargetId}
                onChange={setMergeTargetId}
                options={mergeCandidates.map((m) => ({
                  value: String(m.id),
                  label: ((m.content || "").trim().slice(0, 72) || `#${m.id}`) + (m.content && m.content.length > 72 ? "…" : ""),
                  tooltip: m.content,
                }))}
              />
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <ActionButton onClick={() => setMergePickerOpen(false)} disabled={merging}>
            {t("settingsMemory.mergePicker.cancel")}
          </ActionButton>
          <ActionButton
            kind="destructive"
            disabled={!mergeTargetId || merging}
            onClick={() => setMergeConfirmOpen(true)}
          >
            {t("settingsMemory.mergePicker.merge")}
          </ActionButton>
        </DialogActions>
      </Dialog>

      <ConfirmActionDialog
        open={pendingDelete != null}
        title={pendingDelete === "all" ? t("settingsMemory.deleteAllTitle") : t("settingsMemory.deleteOneTitle")}
        description={
          pendingDelete === "all"
            ? t("settingsMemory.deleteAllDescription")
            : (memories.find((m) => m.id === pendingDelete)?.content || "").slice(0, 160) || null
        }
        facts={pendingDelete === "all" && typeof total === "number" ? [{ label: t("settingsMemory.factMemories"), value: total }] : []}
        confirmLabel={pendingDelete === "all" ? t("settingsMemory.deleteAll") : t("settingsMemory.deleteMemory")}
        onConfirm={() => {
          const target = pendingDelete;
          setPendingDelete(null);
          if (target === "all") clearAllMemories();
          else deleteMemory(target);
        }}
        onClose={() => setPendingDelete(null)}
      />

      <ConfirmActionDialog
        open={mergeConfirmOpen}
        title={t("settingsMemory.mergeConfirm.title")}
        description={t("settingsMemory.mergeConfirm.description")}
        facts={[
          { label: t("settingsMemory.mergeConfirm.source"), value: (editContent || editTarget?.content || "").slice(0, 120) || `#${editTarget?.id}` },
          { label: t("settingsMemory.mergeConfirm.target"), value: (mergeTarget?.content || "").slice(0, 120) || `#${mergeTargetId}` },
        ]}
        keeps={t("settingsMemory.mergeConfirm.keeps")}
        confirmLabel={t("settingsMemory.mergeConfirm.merge")}
        busy={merging}
        onConfirm={confirmMerge}
        onClose={() => !merging && setMergeConfirmOpen(false)}
      />

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
            reload();
          }
        }}
      />
    </SettingsSection>
  );
};

export default MemoryManagementSection;
