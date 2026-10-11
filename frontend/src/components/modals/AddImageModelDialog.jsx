// Add a Hugging Face image model or LoRA to the user image catalog, then Install.
// Role is required: a new generation model of a shipped family (a diffusers repo,
// a single SD / SDXL checkpoint, or a FLUX or Qwen-Image 2.1 UNET) or a LoRA
// stacked on one.

import React, { useEffect, useMemo, useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Checkbox,
  Radio,
  RadioGroup,
  FormControlLabel,
  Typography,
  Box,
  LinearProgress,
} from "@mui/material";
import axios from "axios";
import { ActionButton, ChoiceChips, Hint } from "../settings/ui";
import useHfModelLookup from "../../hooks/useHfModelLookup";
import { formatWeightSize, selectedBytes } from "../../utils/hfUrl";

const ROLE_OPTIONS = [
  { value: "generation", label: "New generation model" },
  { value: "lora", label: "LoRA on a model" },
];

const AddImageModelDialog = ({ open, onClose, showMessage, onAdded }) => {
  const { url, onUrlChange, looking, preview, error, setError, handleLookup } = useHfModelLookup({
    endpoint: "/api/batch-image/models/from-hf",
    open,
  });
  const [role, setRole] = useState("generation");
  const [family, setFamily] = useState("");
  const [selected, setSelected] = useState([]);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [fileFilter, setFileFilter] = useState("");

  const families = preview?.families || [];
  const familyChoices = useMemo(() => {
    if (role === "lora") return families.filter((f) => f.lora);
    if (preview && !preview.has_model_index) return families.filter((f) => f.single_file);
    return families;
  }, [families, role, preview]);
  const familySpec = familyChoices.find((f) => f.id === family);
  // FLUX stills are one ComfyUI UNET, even when the repo has model_index.json.
  const snapshot = Boolean(
    preview?.has_model_index && role === "generation" && familySpec?.engine !== "comfy",
  );
  const singleFile = !snapshot;
  const unwired = preview?.unwired;

  useEffect(() => {
    if (!open) {
      setRole("generation");
      setFamily("");
      setSelected([]);
      setName("");
      setSaving(false);
      setFileFilter("");
    }
  }, [open]);

  useEffect(() => {
    if (preview?.suggested_role) setRole(preview.suggested_role);
    if (preview?.suggested_family) setFamily(preview.suggested_family);
    if (preview?.src) {
      setSelected([preview.src]);
      setName(preview.src.split("/").pop().replace(/\.[^.]+$/, ""));
    } else if (preview?.files?.length === 1) {
      setSelected([preview.files[0].src]);
      setName(preview.files[0].src.split("/").pop().replace(/\.[^.]+$/, ""));
    } else if (preview?.hf_repo) {
      setName(preview.hf_repo.split("/").pop());
    }
  }, [preview]);

  const toggleFile = (src) => {
    if (singleFile) {
      setSelected([src]);
      return;
    }
    setSelected((prev) => (prev.includes(src) ? prev.filter((s) => s !== src) : [...prev, src]));
  };

  const familyOk = familyChoices.some((f) => f.id === family);
  const pending = Boolean(
    preview && !unwired && familyOk && !saving && (snapshot || selected.length === 1),
  );

  const handleAdd = async (install) => {
    if (!pending) return;
    setSaving(true);
    setError("");
    try {
      const files = snapshot
        ? []
        : selected.map((src) => ({
            src,
            size: (preview.files || []).find((f) => f.src === src)?.size || 0,
          }));
      const res = await axios.post("/api/batch-image/models/user", {
        url,
        hf_repo: preview.hf_repo,
        revision: preview.revision,
        role,
        family,
        files,
        name: name.trim() || undefined,
        install,
      });
      if (!res.data.success) {
        setError(res.data.error?.message || res.data.message || "Could not add");
        setSaving(false);
        return;
      }
      const dl = res.data.data?.download;
      if (dl?.error) {
        showMessage?.(
          `Added ${res.data.data?.entry?.name || name}. Install did not start: ${dl.error}`,
          "warning",
        );
      } else if (install) {
        showMessage?.(`Added ${res.data.data?.entry?.name || name}. Installing…`, "info");
      } else {
        showMessage?.(`Added ${res.data.data?.entry?.name || name}. Install when you are ready.`, "info");
      }
      onAdded?.(res.data.data?.id);
      onClose();
    } catch (err) {
      setError(err.response?.data?.error?.message || err.message || "Could not add");
    } finally {
      setSaving(false);
    }
  };

  const files = preview?.files || [];
  const visibleFiles = fileFilter.trim()
    ? files.filter((f) => f.src.toLowerCase().includes(fileFilter.trim().toLowerCase()))
    : files;
  const bytes = snapshot ? 0 : selectedBytes(files, selected);
  const repoUrl = preview?.hf_repo ? `https://huggingface.co/${preview.hf_repo}` : "";

  return (
    <Dialog open={open} onClose={saving ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add new model</DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1.5, pt: 1 }}>
        <Hint>
          Paste a Hugging Face URL or org/repo. A diffusers repo installs whole; a single
          .safetensors is a checkpoint (SD, SDXL) or a LoRA. FLUX and Qwen-Image 2.1 stills are
          one UNET in ComfyUI.
        </Hint>
        <Box sx={{ display: "flex", gap: 1, alignItems: "flex-start" }}>
          <TextField
            size="small"
            fullWidth
            label="Hugging Face URL"
            placeholder="https://huggingface.co/org/repo"
            value={url}
            onChange={(e) => onUrlChange(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleLookup()}
            disabled={looking || saving}
          />
          <ActionButton onClick={handleLookup} loading={looking} disabled={!url.trim()}>
            Look up
          </ActionButton>
        </Box>
        {looking && <LinearProgress />}
        {error && (
          <Typography variant="body2" color="error">
            {error}
          </Typography>
        )}
        {preview && unwired && (
          <Typography variant="body2" color="error">
            {unwired.reason}
          </Typography>
        )}
        {preview && !unwired && (
          <>
            <Typography variant="caption" color="text.secondary">
              {preview.hf_repo}
              {preview.has_model_index ? " · diffusers repo" : " · files only"}
              {preview.license ? ` · ${preview.license}` : ""}
              {preview.gated
                ? preview.token_present
                  ? " · gated (agree on the model page with your HF_TOKEN account)"
                  : " · gated (set HF_TOKEN in .env and restart)"
                : ""}
              {preview.truncated ? " · file list truncated" : ""}
            </Typography>
            {(preview.warnings || []).map((w) => (
              <Typography key={w} variant="caption" color="warning.main">
                {w}
              </Typography>
            ))}
            <ChoiceChips ariaLabel="Role" value={role} onChange={setRole} options={ROLE_OPTIONS} />
            <FormControl size="small" fullWidth>
              <InputLabel>{role === "lora" ? "Applies to" : "Family"}</InputLabel>
              <Select
                value={familyOk ? family : ""}
                label={role === "lora" ? "Applies to" : "Family"}
                onChange={(e) => setFamily(e.target.value)}
              >
                {familyChoices.map((f) => (
                  <MenuItem key={f.id} value={f.id}>
                    {f.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            {familyChoices.length === 0 && (
              <Typography variant="body2" color="error">
                {role === "lora"
                  ? "No family in this list stacks that LoRA."
                  : "No wired family fits this repo. Paste a checkpoint, a FLUX or Qwen-Image 2.1 UNET, or a diffusers repo this product already runs."}
              </Typography>
            )}
            <TextField size="small" label="Name" value={name} onChange={(e) => setName(e.target.value)} />
            {snapshot ? (
              <Hint>The whole repo installs, the same way the shipped models do.</Hint>
            ) : (
              <>
                <Typography variant="overline" color="text.secondary">
                  Files (pick one)
                </Typography>
                {files.length > 8 && (
                  <TextField
                    size="small"
                    label="Filter files"
                    value={fileFilter}
                    onChange={(e) => setFileFilter(e.target.value)}
                  />
                )}
                <Box sx={{ maxHeight: 240, overflow: "auto", border: 1, borderColor: "divider", borderRadius: 1, px: 1 }}>
                  {files.length === 0 && (
                    <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
                      No weight files listed. Paste a URL that includes the filename.
                    </Typography>
                  )}
                  {singleFile ? (
                    <RadioGroup value={selected[0] || ""} onChange={(e) => toggleFile(e.target.value)}>
                      {visibleFiles.map((f) => (
                        <FormControlLabel
                          key={f.src}
                          value={f.src}
                          sx={{ display: "flex", mr: 0 }}
                          control={<Radio size="small" />}
                          label={
                            <Typography variant="body2" sx={{ wordBreak: "break-all" }}>
                              {f.src}
                              {formatWeightSize(f.size)}
                            </Typography>
                          }
                        />
                      ))}
                    </RadioGroup>
                  ) : (
                    visibleFiles.map((f) => (
                      <FormControlLabel
                        key={f.src}
                        sx={{ display: "flex", mr: 0 }}
                        control={
                          <Checkbox size="small" checked={selected.includes(f.src)} onChange={() => toggleFile(f.src)} />
                        }
                        label={
                          <Typography variant="body2" sx={{ wordBreak: "break-all" }}>
                            {f.src}
                            {formatWeightSize(f.size)}
                          </Typography>
                        }
                      />
                    ))
                  )}
                </Box>
              </>
            )}
            <Hint>
              {name.trim() || "Unnamed"} · {familySpec?.label || family || "family"} · {role}
              {bytes > 0 ? ` · ${(bytes / 1024 ** 3).toFixed(2)} GB` : ""}
              {preview.license ? ` · licence ${preview.license}` : ""}
              {preview.gated && repoUrl && (
                <>
                  {" · "}
                  <a href={repoUrl} target="_blank" rel="noreferrer noopener">
                    Agree and access
                  </a>
                </>
              )}
            </Hint>
          </>
        )}
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={onClose} disabled={saving}>
          Cancel
        </ActionButton>
        <ActionButton onClick={() => handleAdd(false)} disabled={!pending}>
          Add only
        </ActionButton>
        <ActionButton kind="primary" onClick={() => handleAdd(true)} loading={saving} disabled={!pending}>
          Add and install
        </ActionButton>
      </DialogActions>
    </Dialog>
  );
};

export default AddImageModelDialog;
