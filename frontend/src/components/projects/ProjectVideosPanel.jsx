import React, { useCallback, useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  CardMedia,
  Chip,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import MusicVideoIcon from "@mui/icons-material/MusicVideo";
import MovieIcon from "@mui/icons-material/Movie";
import { listProjectVideos, updateMusicVideo } from "../../api/musicVideoService";
import { formatUiError } from "../../utils/uiError";
import ConfirmActionDialog from "../settings/ui/ConfirmActionDialog";
import StatusPill from "../settings/ui/StatusPill";
import { translate, useUiTranslation } from "../../i18n";

const POLL_MS = 10000;

/** Where a video project opens. */
export function videoHref(row) {
  if (row.kind === "music_video") return `/music-video?id=${row.id}&project_id=${row.project_id}`;
  return "/film-crew";
}

const kindLabel = (kind) =>
  kind === "music_video"
    ? translate("projects.videos.kindMusicVideo")
    : kind === "film"
      ? translate("projects.videos.kindFilm")
      : kind;

const stageText = () => ({
  draft: [translate("projects.videos.stage.draft"), "neutral"],
  analyzing: [translate("projects.videos.stage.analyzing"), "info"],
  awaiting_approval: [translate("projects.videos.stage.awaitingApproval"), "warn"],
  generating: [translate("projects.videos.stage.generating"), "info"],
  assembling: [translate("projects.videos.stage.assembling"), "info"],
  complete: [translate("projects.videos.stage.complete"), "ok"],
  cancelled: [translate("projects.videos.stage.cancelled"), "neutral"],
});

/** [label, tone] for a row's stage; a failed run says so whatever its stage. */
export function stagePill(row) {
  const status = String(row.status || "");
  if (status.startsWith("failed")) return [translate("projects.videos.stage.failed"), "error"];
  if (status.startsWith("cancelled")) return [translate("projects.videos.stage.cancelled"), "neutral"];
  const known = stageText()[row.current_stage];
  if (known) return known;
  return [String(row.current_stage || "").replace(/_/g, " "), "neutral"];
}

function Thumbnail({ row }) {
  const [broken, setBroken] = useState(false);
  if (row.thumbnail_url && !broken) {
    return <CardMedia component="img" height="110" image={row.thumbnail_url} alt="" onError={() => setBroken(true)} />;
  }
  return (
    <Box sx={{ height: 110, display: "flex", alignItems: "center", justifyContent: "center", bgcolor: "action.hover" }}>
      {row.kind === "music_video" ? <MusicVideoIcon /> : <MovieIcon />}
    </Box>
  );
}

/**
 * The Videos tab of a Project: every music video / Film Crew production filed
 * under it, with its stage and progress, and buttons to start a new one here.
 */
export default function ProjectVideosPanel({ projectId }) {
  const { t } = useUiTranslation();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [unfiling, setUnfiling] = useState(null); // the row the remove dialog asks about
  const [unfileBusy, setUnfileBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await listProjectVideos(projectId);
      setRows(data.videos || []);
      setError(null);
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message || t("projects.videos.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const unfile = async (row) => {
    setUnfileBusy(true);
    try {
      await updateMusicVideo(row.id, { project_id: null });
      setUnfiling(null);
      await load();
    } catch (e) {
      setUnfiling(null);
      setError(formatUiError(e?.response?.data?.error) || e.message || t("projects.videos.removeFailed"));
    } finally {
      setUnfileBusy(false);
    }
  };

  return (
    <Box>
      <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
        <Button
          variant="contained" size="small" startIcon={<MusicVideoIcon />}
          component={RouterLink} to={`/music-video?project_id=${projectId}`}
        >
          {t("projects.videos.newMusicVideo")}
        </Button>
        <Button
          variant="outlined" size="small" startIcon={<MovieIcon />}
          component={RouterLink} to="/film-crew"
        >
          {t("projects.videos.filmCrew")}
        </Button>
      </Stack>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {loading ? (
        <CircularProgress size={24} />
      ) : rows.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          {t("projects.videos.empty")}
        </Typography>
      ) : (
        <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}>
          {rows.map((row) => (
            <Card key={`${row.kind}-${row.id}`} variant="outlined">
              <CardActionArea component={RouterLink} to={videoHref(row)}>
                <Thumbnail row={row} />
                <CardContent sx={{ pb: 1 }}>
                  <Typography variant="subtitle2" noWrap>{row.name}</Typography>
                  <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }} flexWrap="wrap" useFlexGap>
                    <Chip size="small" variant="outlined" label={kindLabel(row.kind)} />
                    <StatusPill label={stagePill(row)[0]} tone={stagePill(row)[1]} />
                    {row.overall_pct != null && row.current_stage !== "complete" && (
                      <Chip size="small" color="primary" variant="outlined" label={`${Math.round(row.overall_pct)}%`} />
                    )}
                  </Stack>
                </CardContent>
              </CardActionArea>
              {row.kind === "music_video" && (
                <Box sx={{ px: 1, pb: 1 }}>
                  <Button size="small" color="inherit" onClick={() => setUnfiling(row)}>
                    {t("projects.videos.removeFromProject")}
                  </Button>
                </Box>
              )}
            </Card>
          ))}
        </Box>
      )}
      <ConfirmActionDialog
        open={!!unfiling}
        title={t("projects.videos.removeTitle", { name: unfiling?.name || "" })}
        description={t("projects.videos.removeDescription")}
        confirmLabel={t("projects.videos.removeFromProject")}
        reversible
        busy={unfileBusy}
        onConfirm={() => unfiling && unfile(unfiling)}
        onClose={() => setUnfiling(null)}
      />
    </Box>
  );
}
