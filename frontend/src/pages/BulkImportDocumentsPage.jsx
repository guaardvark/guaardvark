// frontend/src/pages/BulkImportDocumentsPage.jsx
// Bulk Import: bring a folder into Files and index it for search. Two sources:
// files from this computer (a drop or Choose folder/files, uploaded as copies)
// and a folder on the Guaardvark machine (imported by the worker, which can
// also move). One Into folder, one Import button; the rest under More options.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { Box, Chip, Collapse, MenuItem, Paper, Stack, TextField, Typography } from "@mui/material";
import { useNavigate, useSearchParams } from "react-router-dom";
import { BASE_URL } from "../api/apiClient";
import { useSnackbar } from "../components/common/SnackbarProvider";
import PageLayout from "../components/layout/PageLayout";
import DirectoryPicker from "../components/common/DirectoryPicker";
import TextInputDialog from "../components/common/TextInputDialog";
import { ActionButton, ConfirmActionDialog, SettingChip } from "../components/settings/ui";
import { displayPath, rootLabel } from "../utils/displayPath";
import { ensureFolderPath, filesFolderApi } from "../utils/droppedFiles";
import { startBulkImport, getBulkImportStatus } from "../api/bulkImportService";
import { getClients } from "../api/clientService";
import { getProjects } from "../api/projectService";
import { getWebsites } from "../api/websiteService";
import ImportDropZone from "../components/documents/bulkImport/ImportDropZone";
import ImportStatusCard from "../components/documents/bulkImport/ImportStatusCard";
import ImportActionButton from "../components/documents/bulkImport/ImportActionButton";
import ImportTargetSelect, { DEFAULT_INTO, intoLabel } from "../components/documents/bulkImport/ImportTargetSelect";
import { isTerminalStatus, isSuccessStatus, jobNote, skippedPhrase } from "../components/documents/bulkImport/importStatus";
import { stageDrop, stageFileList, stagedLabel, uploadStaged } from "../components/documents/bulkImport/browserImport";
import { useUiTranslation } from "../i18n";

const POLL_INTERVAL_MS = 3000;
const FILES_API = `${BASE_URL}/files`;
// Per-browser conveniences: the running job (so leaving the page does not lose
// it) and the last Into folder. The job's status lives on the server for an hour.
const JOB_KEY = "guaardvark_bulkImportJob";
const INTO_KEY = "guaardvark_bulkImportInto";
const RECENT_PATHS_KEY = "guaardvark_recentDirectoryPaths"; // DirectoryPicker's own list
const RECENT_CHIPS = 3;
// A drop this large is better imported from the Guaardvark machine itself.
// A guess, not a measurement.
const BIG_DROP_FILES = 2000;
const BIG_DROP_BYTES = 2 * 1024 * 1024 * 1024;

const readJson = (key, fallback) => {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
};
const writeJson = (key, value) => {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Private windows and blocked storage: the page works without it.
  }
};

const formatBytes = (bytes) => {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
};

const BulkImportDocumentsPage = () => {
  const { t, language } = useUiTranslation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { showMessage } = useSnackbar();

  // Where from: null, { kind: "server", path }, or { kind: "browser", files, skipped, tooLarge, totalBytes }.
  const [source, setSource] = useState(null);
  const [into, setInto] = useState(DEFAULT_INTO);
  const [rootFolders, setRootFolders] = useState([]);
  const [reindexMissing, setReindexMissing] = useState(true);
  const [links, setLinks] = useState({ client_id: "", project_id: "", website_id: "" });
  const [entities, setEntities] = useState({ clients: [], projects: [], websites: [] });
  const [moreOpen, setMoreOpen] = useState(false);

  const [pickerOpen, setPickerOpen] = useState(false);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [browseInfo, setBrowseInfo] = useState({ roots: [], home: null });
  const [recentPaths, setRecentPaths] = useState(() => readJson(RECENT_PATHS_KEY, []));

  // The job: a server job { kind: "server", jobId, mode, status, ... } or a
  // browser upload { kind: "browser", status: "uploading" | "complete" | "stopped", ... }.
  const [job, setJob] = useState(null);
  const [starting, setStarting] = useState(false);
  const [moveConfirm, setMoveConfirm] = useState(null);
  const stopUploadRef = useRef(false);

  const serverJobRunning = job?.kind === "server" && !isTerminalStatus(job.status);
  const uploading = job?.kind === "browser" && job.status === "uploading";
  const busy = starting || serverJobRunning || uploading;

  // Load the Into folders, the link lists and the server-folder roots.
  const loadRootFolders = useCallback(async () => {
    try {
      const res = await axios.get(`${FILES_API}/browse`, { params: { path: "/", fields: "light" } });
      const folders = (res.data?.data?.folders || [])
        .filter((f) => Number.isInteger(f.id))
        .map((f) => ({ id: f.id, name: f.name, path: f.path }))
        .sort((a, b) => a.name.localeCompare(b.name));
      setRootFolders(folders);
      return folders;
    } catch {
      return [];
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const folders = await loadRootFolders();
      if (cancelled) return;
      // Opened for one folder (Files › right-click › Bulk import into this folder…).
      const intoId = Number(searchParams.get("into_id"));
      const intoPath = searchParams.get("into");
      if (intoId && intoPath) {
        setInto({ id: intoId, path: intoPath, name: intoPath.split("/").filter(Boolean).pop(), isNew: false });
        return;
      }
      const remembered = readJson(INTO_KEY, null);
      const match = remembered && folders.find((f) => f.id === remembered.id);
      if (match) setInto({ ...match, isNew: false });
      else {
        const imports = folders.find((f) => f.name === DEFAULT_INTO.name);
        if (imports) setInto({ ...imports, isNew: false });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadRootFolders, searchParams]);

  useEffect(() => {
    Promise.all([getClients(), getProjects(), getWebsites()])
      .then(([clients, projects, websites]) =>
        setEntities({
          clients: Array.isArray(clients) ? clients : [],
          projects: Array.isArray(projects) ? projects : [],
          websites: Array.isArray(websites) ? websites : [],
        }))
      .catch(() => {
        // Linking is optional; without the lists its dropdowns stay hidden.
      });
  }, []);

  useEffect(() => {
    // One listing for the roots and the home folder, to offer them as chips
    // and write paths with ~.
    fetch(`${FILES_API}/browse-server?path=`)
      .then((r) => r.json())
      .then((data) => setBrowseInfo({ roots: Array.isArray(data.roots) ? data.roots : [], home: data.home || null }))
      .catch(() => {});
  }, []);

  const chooseInto = (next) => {
    setInto(next);
    writeJson(INTO_KEY, next.id != null ? { id: next.id } : null);
  };

  // ── Server jobs: start, poll, resume ──────────────────────────────────────

  const applyStatus = useCallback((jobId, status, { first = false } = {}) => {
    setJob((prev) => {
      if (!prev || prev.jobId !== jobId) return prev;
      const terminal = isTerminalStatus(status?.status);
      return {
        ...prev,
        status: status?.status || prev.status,
        message: status?.message || prev.message,
        progress: typeof status?.progress === "number" ? status.progress : prev.progress,
        stats: status?.stats && Object.keys(status.stats).length ? status.stats : prev.stats,
        awayNote: first && prev.resumed && terminal ? jobNote("finishedAway") : prev.awayNote,
      };
    });
  }, []);

  const pollOnce = useCallback(async (jobId, opts) => {
    try {
      const status = await getBulkImportStatus(jobId);
      applyStatus(jobId, status, opts);
      if (isTerminalStatus(status?.status)) writeJson(JOB_KEY, null);
      return status;
    } catch (err) {
      // Only a 404 ends it: the server no longer keeps the job. A dropped
      // connection or a restarting backend is temporary, so the import stays
      // tracked (and resumable) and the next poll tries again.
      if (err.status !== 404) {
        setJob((prev) =>
          prev && prev.jobId === jobId
            ? { ...prev, message: jobNote("unreachable") }
            : prev);
        return null;
      }
      writeJson(JOB_KEY, null);
      setJob((prev) =>
        prev && prev.jobId === jobId
          ? {
            ...prev,
            status: "error",
            message: jobNote("expired"),
          }
          : prev);
      return null;
    }
  }, [applyStatus]);

  useEffect(() => {
    if (job?.kind !== "server" || !job.jobId || isTerminalStatus(job.status)) return undefined;
    const interval = setInterval(() => pollOnce(job.jobId), POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [job?.kind, job?.jobId, job?.status, pollOnce]);

  // Coming back to the page picks up the job that was running.
  useEffect(() => {
    const saved = readJson(JOB_KEY, null);
    if (!saved?.jobId) return;
    setJob({ ...saved, kind: "server", status: "processing", resumed: true });
    pollOnce(saved.jobId, { first: true });
  }, [pollOnce]);

  const startServerJob = async ({ mode }) => {
    if (source?.kind !== "server") return;
    setStarting(true);
    const preview = mode === "preview" || mode === "move-preview";
    try {
      const payload = {
        source_path: source.path,
        // The path as well as the id, so a server without target_folder_id
        // still finds a top-level folder by it.
        target_folder: into.path || into.name,
        target_folder_id: into.id ?? null,
        project_id: links.project_id || null,
        client_id: links.client_id || null,
        website_id: links.website_id || null,
        reindex_missing: reindexMissing,
        force_copy: mode !== "move",
        dry_run: preview,
      };
      const response = await startBulkImport(payload);
      if (!response?.job_id) {
        showMessage(response?.message || t("upload.bulk.notStarted"), "error");
        return;
      }
      const record = {
        kind: "server",
        jobId: response.job_id,
        mode,
        preview,
        status: response.status || "queued",
        message: preview ? jobNote("counting") : jobNote("started"),
        sourceLabel: jobNote("fromServer", { path: displayPath(source.path, browseInfo.home) }),
        into: { path: into.path, name: into.name },
      };
      setJob(record);
      writeJson(JOB_KEY, { jobId: record.jobId, mode, preview, sourceLabel: record.sourceLabel, into: record.into });
    } catch (error) {
      showMessage(error.message || t("upload.bulk.startFailed"), "error");
    } finally {
      setStarting(false);
    }
  };

  // A move is counted first, then confirmed with the real numbers.
  useEffect(() => {
    if (job?.kind === "server" && job.mode === "move-preview" && isSuccessStatus(job.status) && !job.moveAsked) {
      const count = Number(job.stats?.total_files) || 0;
      setJob((prev) => ({ ...prev, moveAsked: true, ...(count === 0 ? { message: jobNote("nothingToMove") } : {}) }));
      if (count > 0) {
        setMoveConfirm({ count, skippedPrivate: job.stats?.skipped_private });
      }
    }
  }, [job]);

  // An import into a new folder created it; Into becomes that folder.
  const importFinished = job?.kind === "server" && !job.preview && isSuccessStatus(job.status);
  useEffect(() => {
    if (!importFinished || !into.isNew) return;
    loadRootFolders().then((folders) => {
      const created = folders.find((f) => f.name === into.name);
      if (created) chooseInto({ ...created, isNew: false });
    });
  }, [importFinished, into.isNew, into.name, loadRootFolders]);

  // ── Browser uploads ───────────────────────────────────────────────────────

  const stage = async (staging) => {
    let staged;
    try {
      staged = await staging;
    } catch (err) {
      showMessage(t("upload.bulk.readDropFailed", { reason: err?.message || t("upload.bulk.unknownError") }), "error");
      return;
    }
    if (staged.files.length === 0) {
      const why = [
        skippedPhrase(staged.skipped),
        staged.tooLarge.length ? t("upload.bulk.tooLargeCount", { count: staged.tooLarge.length }) : "",
      ]
        .filter(Boolean)
        .join("; ");
      showMessage(why ? t("upload.bulk.nothingToImportSkipped", { why }) : t("upload.bulk.nothingToImport"), "warning");
      return;
    }
    setJob(null);
    setSource({ kind: "browser", ...staged });
  };

  const startUpload = async () => {
    if (source?.kind !== "browser") return;
    stopUploadRef.current = false;
    const label = stagedLabel(source.files);
    const record = {
      kind: "browser",
      status: "uploading",
      progress: 0,
      message: jobNote("uploadStarting"),
      sourceLabel: label.startsWith('"')
        ? jobNote("fromBrowserFolder", { name: label })
        : jobNote("fromBrowserFiles", { count: source.files.length }),
      into: { path: into.path, name: into.name },
    };
    setJob(record);
    try {
      const baseFolder = into.id != null
        ? into.path
        : await ensureFolderPath(into.name, "/", filesFolderApi(axios, FILES_API));
      const result = await uploadStaged({
        files: source.files,
        baseFolder,
        links: Object.fromEntries(Object.entries(links).filter(([, v]) => v)),
        http: axios,
        filesApiBase: FILES_API,
        shouldStop: () => stopUploadRef.current,
        onProgress: ({ done, total, name }) =>
          setJob((prev) => (prev?.kind === "browser"
            ? {
              ...prev,
              progress: total ? Math.round((done / total) * 100) : 0,
              message: name ? jobNote("uploadingFile", { number: done + 1, total, name }) : prev.message,
            }
            : prev)),
      });
      setJob((prev) => ({
        ...prev,
        status: result.stopped ? "stopped" : "complete",
        progress: 100,
        message: result.stopped
          ? jobNote("uploadStopped", { count: result.imported + result.skipped + result.errors, total: source.files.length })
          : jobNote("uploadFinished"),
        stats: {
          total_files: source.files.length,
          imported: result.imported,
          skipped: result.skipped,
          errors: result.errors,
          queued_for_indexing: result.queued_for_indexing,
          skipped_private: source.skipped,
        },
        failures: result.failures,
        tooLarge: source.tooLarge,
      }));
      if (into.isNew) {
        const folders = await loadRootFolders();
        const created = folders.find((f) => f.path === baseFolder || f.name === into.name);
        if (created) chooseInto({ ...created, isNew: false });
      }
    } catch (err) {
      setJob((prev) => ({ ...prev, status: "error", message: err.message || jobNote("uploadFailed") }));
    }
  };

  useEffect(() => {
    if (!uploading) return undefined;
    const warn = (e) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [uploading]);

  // ── Actions ───────────────────────────────────────────────────────────────

  const startOver = () => {
    stopUploadRef.current = true;
    writeJson(JOB_KEY, null);
    setSource(null);
    setJob(null);
    setMoveConfirm(null);
  };

  const importNow = () => (source?.kind === "browser" ? startUpload() : startServerJob({ mode: "import" }));

  const pickServerFolder = (path) => {
    setJob(null);
    setSource({ kind: "server", path });
    setRecentPaths(readJson(RECENT_PATHS_KEY, []));
  };

  const fileCount = source?.kind === "browser" ? source.files.length : null;
  const importLabel = fileCount != null
    ? t("upload.bulk.action.importFiles", { count: fileCount })
    : t("upload.bulk.action.import");
  const disabledReason = !source
    ? t("upload.bulk.chooseFirst")
    : busy
    ? t("upload.bulk.waitForImport")
    : "";

  // rootLabel words the home folder, so the chips follow the language too.
  const serverChips = useMemo(() => {
    const roots = browseInfo.roots.map((r) => ({ path: r, label: rootLabel(r, browseInfo.home) }));
    const recent = recentPaths
      .filter((p) => !browseInfo.roots.includes(p))
      .slice(0, RECENT_CHIPS)
      .map((p) => ({ path: p, label: displayPath(p, browseInfo.home) }));
    return [...recent, ...roots];
  }, [browseInfo, recentPaths, language]);

  const linkKinds = [
    { key: "client_id", label: t("upload.bulk.links.client"), items: entities.clients, name: (c) => c.name },
    { key: "project_id", label: t("upload.bulk.links.project"), items: entities.projects, name: (p) => p.name },
    { key: "website_id", label: t("upload.bulk.links.website"), items: entities.websites, name: (w) => w.url },
  ].filter((k) => k.items.length > 0);

  const moveSkipped = moveConfirm ? skippedPhrase(moveConfirm.skippedPrivate) : "";

  const bigDrop = source?.kind === "browser" &&
    (source.files.length > BIG_DROP_FILES || source.totalBytes > BIG_DROP_BYTES);

  const stagedName = source?.kind === "browser" ? stagedLabel(source.files) : "";
  const filesAndSize = source?.kind === "browser"
    ? t("upload.bulk.from.filesAndSize", { count: source.files.length, size: formatBytes(source.totalBytes) })
    : "";
  const fromLine = source && (
    <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ xs: "flex-start", sm: "center" }}>
      <Typography variant="overline" color="text.secondary" sx={{ lineHeight: 1.5 }}>
        {t("upload.bulk.from.label")}
      </Typography>
      <Typography variant="body2" sx={{ flexGrow: 1, minWidth: 0, overflowWrap: "anywhere" }} data-testid="import-from">
        {source.kind === "server" ? (
          <>
            {displayPath(source.path, browseInfo.home)}
            <Typography component="span" variant="body2" color="text.secondary">
              {" · "}{t("upload.bulk.from.serverNote")}
            </Typography>
          </>
        ) : (
          <>
            {stagedName.startsWith('"') ? `${stagedName} · ` : ""}
            {filesAndSize}
            <Typography component="span" variant="body2" color="text.secondary">
              {" · "}{t("upload.bulk.from.browserNote")}
              {skippedPhrase(source.skipped)
                ? ` · ${t("upload.bulk.result.skipped", { what: skippedPhrase(source.skipped) })}`
                : ""}
              {source.tooLarge.length
                ? ` · ${t("upload.bulk.from.tooLargeLeftOut", { count: source.tooLarge.length })}`
                : ""}
            </Typography>
          </>
        )}
      </Typography>
      <Stack direction="row" spacing={1}>
        {source.kind === "server" && (
          <ActionButton onClick={() => setPickerOpen(true)} disabled={busy}>
            {t("upload.bulk.from.change")}
          </ActionButton>
        )}
        <ActionButton onClick={() => setSource(null)} disabled={busy}>
          {t("upload.bulk.from.clear")}
        </ActionButton>
      </Stack>
    </Stack>
  );

  return (
    <PageLayout
      title={t("upload.bulk.title")}
      variant="standard"
      actions={
        <Stack direction="row" spacing={1}>
          {(source || job) && (
            <ActionButton onClick={startOver} disabled={starting}>
              {t("upload.bulk.startOver")}
            </ActionButton>
          )}
          <ActionButton kind="link" onClick={() => navigate("/documents")}>
            {t("upload.bulk.filesPage")}
          </ActionButton>
        </Stack>
      }
    >
      <Stack spacing={2}>
        <Typography variant="body2" color="text.secondary">
          {t("upload.bulk.intro")}
        </Typography>

        <Paper variant="outlined" sx={{ p: 2 }}>
          <Stack spacing={1.5}>
            <ImportDropZone
              compact={Boolean(source)}
              disabled={busy}
              disabledReason={t("upload.bulk.waitForImport")}
              onDrop={(snapshot) => stage(stageDrop(snapshot))}
              onPick={(files) => stage(Promise.resolve(stageFileList(files)))}
            >
              {fromLine}
            </ImportDropZone>

            {!source && (
              <Stack spacing={1}>
                <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ xs: "stretch", sm: "center" }}>
                  <Typography variant="body2" color="text.secondary">
                    {t("upload.bulk.orServerFolder")}
                  </Typography>
                  <ActionButton
                    onClick={() => setPickerOpen(true)}
                    disabled={busy}
                    sx={{ whiteSpace: { xs: "normal", sm: "nowrap" }, height: { xs: "auto", sm: 30 }, py: { xs: 0.75, sm: 0 } }}
                  >
                    {t("upload.bulk.browseServer")}
                  </ActionButton>
                </Stack>
                {serverChips.length > 0 && (
                  <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                    {serverChips.map((c) => (
                      <Chip
                        key={c.path}
                        label={c.label}
                        size="small"
                        variant="outlined"
                        onClick={() => pickServerFolder(c.path)}
                        disabled={busy}
                      />
                    ))}
                  </Stack>
                )}
              </Stack>
            )}

            {bigDrop && (
              <Typography variant="caption" color="text.secondary">
                {t("upload.bulk.bigDrop")}
              </Typography>
            )}
          </Stack>
        </Paper>

        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ xs: "stretch", sm: "center" }}>
          <ImportTargetSelect
            folders={rootFolders}
            value={into}
            onChange={chooseInto}
            onNewFolder={() => setNewFolderOpen(true)}
            disabled={busy}
          />
          <ActionButton kind="link" onClick={() => setMoreOpen((v) => !v)}>
            {moreOpen ? t("upload.bulk.fewerOptions") : t("upload.bulk.moreOptions")}
          </ActionButton>
          <Box sx={{ flexGrow: 1 }} />
          <ImportActionButton
            label={importLabel}
            disabled={!source || busy}
            disabledReason={disabledReason}
            canPreview={source?.kind === "server"}
            canMove={source?.kind === "server"}
            onImport={importNow}
            onPreview={() => startServerJob({ mode: "preview" })}
            onMove={() => startServerJob({ mode: "move-preview" })}
          />
        </Stack>

        <Collapse in={moreOpen} unmountOnExit>
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack spacing={1.5}>
              <Box>
                <SettingChip
                  label={t("upload.bulk.reindexMissing")}
                  on={reindexMissing}
                  onToggle={setReindexMissing}
                  disabled={busy}
                  tooltip={t("upload.bulk.reindexMissingTooltip")}
                />
              </Box>
              {linkKinds.length > 0 && (
                <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ xs: "stretch", sm: "center" }}>
                  <Typography variant="overline" color="text.secondary">
                    {t("upload.bulk.links.linkTo")}
                  </Typography>
                  {linkKinds.map((kind) => (
                    <TextField
                      key={kind.key}
                      select
                      size="small"
                      label={kind.label}
                      value={links[kind.key]}
                      onChange={(e) => setLinks((prev) => ({ ...prev, [kind.key]: e.target.value }))}
                      disabled={busy}
                      sx={{ minWidth: 180 }}
                    >
                      <MenuItem value="">{t("upload.bulk.links.none")}</MenuItem>
                      {kind.items.map((item) => (
                        <MenuItem key={item.id} value={item.id}>
                          {kind.name(item)}
                        </MenuItem>
                      ))}
                    </TextField>
                  ))}
                </Stack>
              )}
            </Stack>
          </Paper>
        </Collapse>

        <ImportStatusCard
          job={job}
          onStop={uploading ? () => { stopUploadRef.current = true; } : undefined}
          onOpenFiles={() => {
            const target = into.path || into.name;
            navigate(target ? `/documents?open=${encodeURIComponent(target)}` : "/documents");
          }}
          onImportAnother={startOver}
        />
      </Stack>

      <DirectoryPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onSelect={(path) => pickServerFolder(path)}
        initialPath={source?.kind === "server" ? source.path : "~"}
        title={t("upload.bulk.pickerTitle")}
      />

      <TextInputDialog
        open={newFolderOpen}
        title={t("upload.bulk.newFolder.title")}
        description={t("upload.bulk.newFolder.description")}
        label={t("upload.bulk.newFolder.label")}
        submitLabel={t("upload.bulk.newFolder.submit")}
        onSubmit={(name) => {
          setNewFolderOpen(false);
          const existing = rootFolders.find((f) => f.name === name);
          chooseInto(existing ? { ...existing, isNew: false } : { id: null, path: null, name, isNew: true });
        }}
        onClose={() => setNewFolderOpen(false)}
      />

      <ConfirmActionDialog
        open={Boolean(moveConfirm)}
        title={t("upload.bulk.move.title")}
        description={t("upload.bulk.move.description")}
        facts={moveConfirm ? [
          { label: t("upload.bulk.move.filesFound"), value: moveConfirm.count },
          { label: t("upload.bulk.from.label"), value: source?.kind === "server" ? displayPath(source.path, browseInfo.home) : "" },
          { label: t("upload.bulk.into.label"), value: intoLabel(into) },
        ] : []}
        keeps={moveSkipped
          ? t("upload.bulk.move.keepsWithSkipped", { skipped: moveSkipped })
          : t("upload.bulk.move.keeps")}
        confirmLabel={t("upload.bulk.move.confirm")}
        onConfirm={() => {
          setMoveConfirm(null);
          startServerJob({ mode: "move" });
        }}
        onClose={() => setMoveConfirm(null)}
      />
    </PageLayout>
  );
};

export default BulkImportDocumentsPage;
