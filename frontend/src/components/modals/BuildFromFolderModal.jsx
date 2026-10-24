// Build from folder: turn a folder of your own notes into a training dataset.
// Check first (what is read and what is scrubbed, then a timed sample of three
// passages), and only then Build. Optionally also copies the folder into
// Files and indexes it, which is what lets questions about exact facts be
// answered from the notes themselves.
import React, { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  LinearProgress,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import DirectoryPicker from "../common/DirectoryPicker";
import { ActionButton, SettingChip, StatusPill } from "../settings/ui";
import {
  checkFolderBuild,
  getScrubTerms,
  probeFolderBuild,
  saveScrubTerms,
  startFolderBuild,
} from "../../api/trainingService";
import { startBulkImport } from "../../api/bulkImportService";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

const kindChoices = () => [
  { ext: ".md", label: translate("dialogs.buildFromFolder.kinds.markdown") },
  { ext: ".txt", label: translate("dialogs.buildFromFolder.kinds.text") },
  { ext: ".html", label: translate("dialogs.buildFromFolder.kinds.html") },
  { ext: ".docx", label: translate("dialogs.buildFromFolder.kinds.word") },
  { ext: ".pdf", label: translate("dialogs.buildFromFolder.kinds.pdf") },
];
// What the build aims for. "enough" is what a default fine-tune can use, with
// headroom for the filter (the server says the number).
const TARGETS = ["enough", 5000, 2000, "all"];
const ruleLabels = () => ({
  private_key: translate("dialogs.buildFromFolder.rules.privateKey"),
  api_key: translate("dialogs.buildFromFolder.rules.apiKey"),
  recovery_key: translate("dialogs.buildFromFolder.rules.recoveryKey"),
  recovery_code: translate("dialogs.buildFromFolder.rules.recoveryCode"),
  bearer_header: translate("dialogs.buildFromFolder.rules.bearerHeader"),
  api_key_header: translate("dialogs.buildFromFolder.rules.apiKeyHeader"),
  secret_assignment: translate("dialogs.buildFromFolder.rules.secretAssignment"),
  email: translate("dialogs.buildFromFolder.rules.email"),
  private_ip: translate("dialogs.buildFromFolder.rules.privateIp"),
  lan_host: translate("dialogs.buildFromFolder.rules.lanHost"),
  word_list: translate("dialogs.buildFromFolder.rules.wordList"),
});

const fileCount = (count) => translate("dialogs.buildFromFolder.counts.file", { count });
const passageCount = (count) => translate("dialogs.buildFromFolder.counts.passage", { count });
const itemCount = (count) => translate("dialogs.buildFromFolder.counts.item", { count });
const folderName = (path) => String(path || "").replace(/\/+$/, "").split("/").pop() || "Notes";

const formatDuration = (seconds) => {
  if (seconds == null) return null;
  if (seconds < 90) return translate("dialogs.buildFromFolder.duration.seconds", { count: Math.max(1, Math.round(seconds)) });
  if (seconds < 5400) return translate("dialogs.buildFromFolder.duration.minutes", { count: Math.round(seconds / 60) });
  return translate("dialogs.buildFromFolder.duration.hours", { hours: (seconds / 3600).toFixed(1) });
};

const skippedLine = (skipped) => {
  if (!skipped) return "";
  const folders = (skipped.hidden_dirs || 0) + (skipped.internal_dirs || 0);
  const files = (skipped.hidden_files || 0) + (skipped.sensitive_files || 0) + (skipped.symlinks || 0);
  const parts = [];
  if (folders) parts.push(translate("dialogs.buildFromFolder.skipped.hiddenFolders", { count: folders }));
  if (files) parts.push(translate("dialogs.buildFromFolder.skipped.hiddenFiles", { count: files }));
  if (skipped.package_files) {
    parts.push(
      translate("dialogs.buildFromFolder.skipped.packageFiles", {
        count: skipped.package_files,
        folders: translate("dialogs.buildFromFolder.skipped.copiedFolders", { count: skipped.package_dirs || 0 }),
      }),
    );
  }
  if (skipped.not_notes) parts.push(translate("dialogs.buildFromFolder.skipped.notNotes", { count: skipped.not_notes }));
  if (skipped.not_prose) parts.push(translate("dialogs.buildFromFolder.skipped.notProse", { count: skipped.not_prose }));
  if (skipped.credential_names) parts.push(translate("dialogs.buildFromFolder.skipped.credentialNames", { count: skipped.credential_names }));
  if (skipped.other_kinds) parts.push(translate("dialogs.buildFromFolder.skipped.otherKinds", { count: skipped.other_kinds }));
  return parts.length ? translate("dialogs.buildFromFolder.skipped.line", { items: parts.join(", ") }) : "";
};

const targetLabel = (value, estimate) => {
  if (value === "enough") {
    return estimate?.enough_to_train
      ? translate("dialogs.buildFromFolder.enoughCount", { count: estimate.enough_to_train.toLocaleString(uiLocale()) })
      : translate("dialogs.buildFromFolder.enough");
  }
  if (value === "all") return translate("dialogs.buildFromFolder.allPassages");
  return Number(value).toLocaleString(uiLocale());
};

const BuildFromFolderModal = ({ open, onClose, onStarted }) => {
  const { t } = useUiTranslation();
  const [folder, setFolder] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [kinds, setKinds] = useState([".md", ".txt"]);
  const [target, setTarget] = useState("enough");
  // Files over the size limit: path -> words, as the checks have reported them,
  // and the ones the person chose to include anyway.
  const [largeFiles, setLargeFiles] = useState({});
  const [includeLarge, setIncludeLarge] = useState([]);
  // Off by default: the copy is a Bulk Import of the whole folder, which does not
  // apply this build's skips (credential-named files, copied packages, huge files).
  const [alsoIndex, setAlsoIndex] = useState(false);
  const [terms, setTerms] = useState("");
  // "loading" | "loaded" | "failed". A list that failed to load is never saved
  // over unless the person edits it, or Check would wipe the saved names.
  const [termsState, setTermsState] = useState("loading");
  const [termsEdited, setTermsEdited] = useState(false);
  const [check, setCheck] = useState(null);
  const [probe, setProbe] = useState(null);
  const [estimate, setEstimate] = useState(null);
  const [busy, setBusy] = useState(null); // "check" | "probe" | "build"
  const [error, setError] = useState(null);
  const [probeError, setProbeError] = useState(null);
  const [showSamples, setShowSamples] = useState(false);

  useEffect(() => {
    if (!open) return;
    setCheck(null);
    setProbe(null);
    setEstimate(null);
    setError(null);
    setProbeError(null);
    setTermsState("loading");
    setTermsEdited(false);
    getScrubTerms()
      .then((list) => {
        setTerms(list.join("\n"));
        setTermsState("loaded");
      })
      .catch(() => {
        setTerms("");
        setTermsState("failed");
      });
  }, [open]);

  // Any change to what would be read makes the last check stale.
  // Another folder: the size list and the inclusions were the old folder's.
  const forgetLargeFiles = () => {
    setLargeFiles({});
    setIncludeLarge([]);
  };

  const invalidate = () => {
    setCheck(null);
    setProbe(null);
    setEstimate(null);
    setProbeError(null);
  };

  const body = () => ({ folder, kinds, target_pairs: target, include_large_files: includeLarge });

  const runCheck = async () => {
    setError(null);
    setProbeError(null);
    setBusy("check");
    try {
      if (termsState === "loaded" || termsEdited) {
        await saveScrubTerms(terms.split("\n"));
      }
      const result = await checkFolderBuild(body());
      setCheck(result);
      setLargeFiles((prev) => ({
        ...prev,
        ...Object.fromEntries((result.skipped?.large_files || []).map((f) => [f.source_path, f.words])),
      }));
      setEstimate(result.estimate);
      setProbe(null);
      if (result.files > 0) {
        setBusy("probe");
        try {
          const timed = await probeFolderBuild(body());
          setProbe(timed.probe);
          setEstimate(timed.estimate);
        } catch (e) {
          setProbeError(e.message || t("dialogs.buildFromFolder.probeFailed"));
        }
      }
    } catch (e) {
      setError(e.message || t("dialogs.buildFromFolder.checkFailed"));
    } finally {
      setBusy(null);
    }
  };

  const runBuild = async () => {
    setError(null);
    setBusy("build");
    try {
      const job = await startFolderBuild(body());
      let indexNote = "";
      if (alsoIndex) {
        try {
          await startBulkImport({
            source_path: folder,
            target_folder: folderName(folder),
            force_copy: true,
            reindex_missing: true,
            dry_run: false,
          });
          indexNote = " " + t("dialogs.buildFromFolder.alsoIndexing", { folder: folderName(folder) });
        } catch (e) {
          indexNote = " " + t("dialogs.buildFromFolder.indexFailed", { error: e.message });
        }
      }
      onStarted?.(job, indexNote);
      onClose();
    } catch (e) {
      setError(e.message || t("dialogs.buildFromFolder.buildFailed"));
    } finally {
      setBusy(null);
    }
  };

  const scrub = check?.scrub;
  const rulesHit = scrub ? Object.entries(scrub.by_rule || {}).filter(([, n]) => n > 0) : [];
  const nothingToRead = check && check.files === 0;
  const modelMissing = Boolean(probeError);
  const labels = ruleLabels();

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="md" fullWidth>
      <DialogTitle>{t("dialogs.buildFromFolder.title")}</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          <Typography variant="body2" color="text.secondary">
            {t("dialogs.buildFromFolder.intro")}
          </Typography>

          <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ xs: "stretch", sm: "center" }}>
            <TextField
              size="small"
              label={t("dialogs.buildFromFolder.folder")}
              value={folder}
              onChange={(e) => {
                setFolder(e.target.value);
                forgetLargeFiles();
                invalidate();
              }}
              placeholder={t("dialogs.buildFromFolder.folderPlaceholder")}
              sx={{ flexGrow: 1 }}
              disabled={Boolean(busy)}
            />
            <ActionButton onClick={() => setPickerOpen(true)} disabled={Boolean(busy)}>
              {t("dialogs.buildFromFolder.browse")}
            </ActionButton>
          </Stack>

          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
            <Typography variant="overline" color="text.secondary">
              {t("dialogs.buildFromFolder.read")}
            </Typography>
            {kindChoices().map((k) => (
              <SettingChip
                key={k.ext}
                label={k.label}
                on={kinds.includes(k.ext)}
                disabled={Boolean(busy)}
                onToggle={(on) => {
                  setKinds((prev) => (on ? [...prev, k.ext] : prev.filter((x) => x !== k.ext)));
                  invalidate();
                }}
              />
            ))}
          </Stack>

          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ xs: "stretch", sm: "center" }}>
            <TextField
              select
              size="small"
              label={t("dialogs.buildFromFolder.pairsToGenerate")}
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              disabled={Boolean(busy)}
              sx={{ minWidth: 260 }}
              SelectProps={{ renderValue: (value) => targetLabel(value, estimate) }}
            >
              {TARGETS.map((value) => {
                const choice = estimate?.choices?.[String(value)];
                return (
                  <MenuItem key={value} value={value}>
                    <Box>
                      <Typography variant="body2">{targetLabel(value, estimate)}</Typography>
                      {choice && (
                        <Typography variant="caption" color="text.secondary">
                          {passageCount(choice.passages)}
                          {choice.seconds != null ? `, ${formatDuration(choice.seconds)}` : ""}
                        </Typography>
                      )}
                    </Box>
                  </MenuItem>
                );
              })}
            </TextField>
            <SettingChip
              label={t("dialogs.buildFromFolder.alsoIndex")}
              on={alsoIndex}
              onToggle={setAlsoIndex}
              disabled={Boolean(busy)}
              tooltip={t("dialogs.buildFromFolder.alsoIndexTooltip")}
            />
          </Stack>

          <TextField
            label={t("dialogs.buildFromFolder.scrubWords")}
            helperText={
              termsState === "failed" && !termsEdited
                ? t("dialogs.buildFromFolder.scrubLoadFailed")
                : t("dialogs.buildFromFolder.scrubHelp")
            }
            value={terms}
            onChange={(e) => {
              setTerms(e.target.value);
              setTermsEdited(true);
              invalidate();
            }}
            multiline
            minRows={3}
            maxRows={8}
            size="small"
            disabled={Boolean(busy)}
          />
          <Typography variant="caption" color="text.secondary">
            {t("dialogs.buildFromFolder.alwaysScrubbed")}
          </Typography>

          {busy && (busy === "check" || busy === "probe") && (
            <Box>
              <LinearProgress />
              <Typography variant="caption" color="text.secondary">
                {busy === "check" ? t("dialogs.buildFromFolder.reading") : t("dialogs.buildFromFolder.timing")}
              </Typography>
            </Box>
          )}
          {error && <Alert severity="error">{error}</Alert>}

          {check && (
            <Box data-testid="build-check">
              <Divider sx={{ mb: 1.5 }} />
              <Stack spacing={1}>
                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                  <StatusPill tone={nothingToRead ? "warn" : "ok"} label={nothingToRead ? t("dialogs.buildFromFolder.nothingToRead") : t("dialogs.buildFromFolder.checked")} />
                  <Typography variant="body2">
                    {t("dialogs.buildFromFolder.filesAndWords", { files: fileCount(check.files), words: check.words.toLocaleString(uiLocale()) })}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {skippedLine(check.skipped)}
                  </Typography>
                </Stack>
                {Object.keys(largeFiles).length > 0 && (
                  <Box>
                    <Typography variant="body2">
                      {t("dialogs.buildFromFolder.largeFiles")}
                    </Typography>
                    <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap sx={{ mt: 0.5 }}>
                      {Object.entries(largeFiles).map(([path, words]) => (
                        <SettingChip
                          key={path}
                          label={path.split("/").pop()}
                          note={t("dialogs.buildFromFolder.words", { words: words.toLocaleString(uiLocale()) })}
                          tooltip={path}
                          on={includeLarge.includes(path)}
                          disabled={Boolean(busy)}
                          onToggle={(on) => {
                            setIncludeLarge((prev) => (on ? [...prev, path] : prev.filter((p) => p !== path)));
                            invalidate();
                          }}
                        />
                      ))}
                    </Stack>
                  </Box>
                )}
                {check.skipped?.credential_files?.length > 0 && (
                  <Alert severity="info">
                    {t("dialogs.buildFromFolder.credentialFiles", { files: check.skipped.credential_files.join(", ") })}
                  </Alert>
                )}
                {check.unreadable?.length > 0 && (
                  <Alert severity="warning">{t("dialogs.buildFromFolder.unreadable", { files: fileCount(check.unreadable.length), first: check.unreadable[0] })}</Alert>
                )}
                <Typography variant="subtitle2">{t("dialogs.buildFromFolder.scrubbed", { items: itemCount(scrub?.total || 0) })}</Typography>
                {rulesHit.length > 0 && (
                  <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                    {rulesHit.map(([rule, n]) => (
                      <StatusPill key={rule} tone="info" label={`${labels[rule] || rule}: ${n}`} />
                    ))}
                  </Stack>
                )}
                {scrub?.samples && Object.keys(scrub.samples).length > 0 && (
                  <Box>
                    <ActionButton kind="link" onClick={() => setShowSamples((v) => !v)}>
                      {showSamples ? t("dialogs.buildFromFolder.hideExamples") : t("dialogs.buildFromFolder.showExamples")}
                    </ActionButton>
                    <Collapse in={showSamples} unmountOnExit>
                      <Box sx={{ mt: 0.5, pl: 1, borderLeft: 2, borderColor: "divider" }}>
                        {Object.entries(scrub.samples).map(([rule, lines]) => (
                          <Box key={rule} sx={{ mb: 1 }}>
                            <Typography variant="caption" color="text.secondary">
                              {labels[rule] || rule}
                            </Typography>
                            {lines.map((line, i) => (
                              <Typography key={i} variant="body2" sx={{ fontFamily: "monospace", fontSize: "0.75rem", overflowWrap: "anywhere" }}>
                                {line}
                              </Typography>
                            ))}
                          </Box>
                        ))}
                      </Box>
                    </Collapse>
                  </Box>
                )}
                {estimate && !nothingToRead && (() => {
                  const choice = estimate.choices?.[String(target)] || {
                    passages: estimate.chunks, pairs_before_filter: estimate.pairs_before_filter, seconds: estimate.seconds,
                  };
                  return (
                  <Typography variant="body2">
                    {choice.seconds != null
                      ? t("dialogs.buildFromFolder.estimateTimed", {
                          target: targetLabel(target, estimate),
                          passages: passageCount(choice.passages),
                          available: estimate.passages_available?.toLocaleString(uiLocale()),
                          pairs: choice.pairs_before_filter.toLocaleString(uiLocale()),
                          duration: formatDuration(choice.seconds),
                          model: probe?.model || t("dialogs.buildFromFolder.localModel"),
                        })
                      : t("dialogs.buildFromFolder.estimate", {
                          target: targetLabel(target, estimate),
                          passages: passageCount(choice.passages),
                          available: estimate.passages_available?.toLocaleString(uiLocale()),
                          pairs: choice.pairs_before_filter.toLocaleString(uiLocale()),
                        })}
                    <Typography component="span" variant="caption" color="text.secondary">
                      {" "}
                      {probe
                        ? t("dialogs.buildFromFolder.timedOn", { passages: passageCount(probe.chunks), seconds: probe.seconds_per_chunk })
                        : t("dialogs.buildFromFolder.chunkSize", { basis: estimate.basis })}
                    </Typography>
                  </Typography>
                  );
                })()}
                {probeError && (
                  <Alert severity="warning">
                    {t("dialogs.buildFromFolder.needsModel", { error: probeError })}
                  </Alert>
                )}
              </Stack>
            </Box>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={onClose} disabled={busy === "build"}>
          {t("dialogs.shared.cancel")}
        </ActionButton>
        <ActionButton
          onClick={runCheck}
          loading={busy === "check" || busy === "probe"}
          disabled={!folder.trim() || kinds.length === 0 || Boolean(busy)}
        >
          {t("dialogs.buildFromFolder.check")}
        </ActionButton>
        <ActionButton
          kind="primary"
          onClick={runBuild}
          loading={busy === "build"}
          disabled={!check || nothingToRead || modelMissing || Boolean(busy)}
          tooltip={!check ? t("dialogs.buildFromFolder.checkFirst") : ""}
        >
          {t("dialogs.buildFromFolder.build")}
        </ActionButton>
      </DialogActions>

      <DirectoryPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onSelect={(path) => {
          setFolder(path);
          forgetLargeFiles();
          invalidate();
        }}
        initialPath={folder || "~"}
        title={t("dialogs.buildFromFolder.pickerTitle")}
        purpose="training"
      />
    </Dialog>
  );
};

export default BuildFromFolderModal;
