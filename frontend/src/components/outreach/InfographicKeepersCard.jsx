// Which infographics outreach may attach to a post. A person marks keepers;
// outreach uses a keeper only after the text read back from the rendered
// image matched what was asked for. Each tile states that read-back exactly:
// nothing here is shown as checked unless the API says it was.
import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  FormControlLabel,
  Paper,
  Stack,
  Switch,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import ImageLightbox from "../images/ImageLightbox";
import {
  fetchOutreachInfographics,
  outreachMediaUrl,
  rereadInfographic,
  setInfographicKeeper,
} from "../../api/outreachService";
import { guardedMediaSrc } from "../../utils/assetGuard";
import { uiLocale, useUiTranslation } from "../../i18n";

// The images are full size (about 1 MB each), so a page is kept small and the
// tiles below the fold load only when scrolled to.
const PAGE_SIZE = 24;
const READING_POLL_MS = 3000;

const statusOf = (item) => item?.readback?.status || "unread";

const Readback = ({ item }) => {
  const { t } = useUiTranslation();
  const rb = item.readback || {};
  const status = statusOf(item);
  const missing = rb.missing || [];
  const extra = rb.extra || [];
  const expected = rb.expected || [];
  const when = rb.checked_at ? new Date(rb.checked_at) : null;

  const lines = (label, list) =>
    list.length > 0 && (
      <Box>
        <Typography variant="caption" color="text.secondary">
          {label}
        </Typography>
        <Box component="ul" sx={{ m: 0, pl: 2 }}>
          {list.map((line, idx) => (
            <Typography key={`${idx}-${line}`} component="li" variant="caption">
              {line}
            </Typography>
          ))}
        </Box>
      </Box>
    );

  let headline;
  if (status === "passed") {
    headline = (
      <Typography variant="caption" color="success.main">
        {expected.length
          ? t("outreach.infographics.readback.passedCount", { total: expected.length })
          : t("outreach.infographics.readback.passed")}
      </Typography>
    );
  } else if (status === "failed") {
    headline = (
      <Typography variant="caption" color="error.main">
        {t("outreach.infographics.readback.failed")}
      </Typography>
    );
  } else if (status === "reading") {
    headline = (
      <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
        <CircularProgress size={12} />
        <Typography variant="caption">{t("outreach.infographics.readback.reading")}</Typography>
      </Stack>
    );
  } else {
    headline = (
      <Typography variant="caption" color="text.secondary">
        {t("outreach.infographics.readback.unread")}
      </Typography>
    );
  }

  const settled = status === "passed" || status === "failed";
  return (
    <Stack spacing={0.5} data-testid={`readback-${item.document_id}`} data-status={status}>
      {headline}
      {status === "failed" && lines(t("outreach.infographics.readback.missing"), missing)}
      {settled && lines(t("outreach.infographics.readback.extra"), extra)}
      {status === "failed" && rb.transcript && (
        <Typography variant="caption" color="text.secondary" sx={{ wordBreak: "break-word" }}>
          {t("outreach.infographics.readback.transcript", { text: rb.transcript })}
        </Typography>
      )}
      {settled && when && !Number.isNaN(when.getTime()) && (
        <Typography variant="caption" color="text.secondary">
          {rb.model
            ? t("outreach.infographics.readback.checkedBy", { model: rb.model, date: when.toLocaleString(uiLocale()) })
            : t("outreach.infographics.readback.checkedAt", { date: when.toLocaleString(uiLocale()) })}
        </Typography>
      )}
    </Stack>
  );
};

const InfographicKeepersCard = () => {
  const { t } = useUiTranslation();
  const [filter, setFilter] = useState("keepers");
  const [items, setItems] = useState(null);
  const [shown, setShown] = useState(PAGE_SIZE);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState(null);
  const [lightboxIndex, setLightboxIndex] = useState(null);

  const refresh = useCallback(async () => {
    try {
      setItems(await fetchOutreachInfographics({ keepers: filter === "keepers" }));
    } catch (err) {
      setError(err.message);
      setItems((prev) => prev || []);
    }
  }, [filter]);

  useEffect(() => {
    setItems(null);
    setShown(PAGE_SIZE);
    setLightboxIndex(null);
    refresh();
  }, [refresh]);

  // A read-back ends without a click here.
  const reading = (items || []).some((item) => statusOf(item) === "reading");
  useEffect(() => {
    if (!reading) return undefined;
    const timer = setInterval(refresh, READING_POLL_MS);
    return () => clearInterval(timer);
  }, [reading, refresh]);

  // The answer replaces its tile. An item switched off under "Keepers" stays
  // until the list is next loaded, so the switch can be put back.
  const act = async (item, action) => {
    setBusyId(item.document_id);
    setError(null);
    try {
      const next = await action();
      if (next?.document_id != null) {
        setItems((prev) => (prev || []).map((it) => (it.document_id === next.document_id ? next : it)));
      } else {
        await refresh();
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  };

  const list = items || [];
  const visible = list.slice(0, shown);
  const usableCount = list.filter((item) => item.usable).length;
  const lightboxItem = lightboxIndex != null ? visible[lightboxIndex] : null;

  return (
    <Paper sx={{ p: 2, mt: 2 }}>
      <Stack direction="row" spacing={1} useFlexGap sx={{ alignItems: "center", flexWrap: "wrap", mb: 0.5 }}>
        <Typography variant="subtitle2" sx={{ flex: 1 }}>
          {t("outreach.infographics.title")}
        </Typography>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={filter}
          onChange={(_e, value) => value && setFilter(value)}
          aria-label={t("outreach.infographics.filterLabel")}
        >
          <ToggleButton value="keepers">{t("outreach.infographics.filterKeepers")}</ToggleButton>
          <ToggleButton value="all">{t("outreach.infographics.filterAll")}</ToggleButton>
        </ToggleButtonGroup>
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        {t("outreach.infographics.explain")}
      </Typography>

      {items == null ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <CircularProgress size={20} />
        </Box>
      ) : list.length === 0 ? (
        !error && (
          <Typography variant="body2" color="text.secondary">
            {t(filter === "keepers" ? "outreach.infographics.emptyKeepers" : "outreach.infographics.emptyAll")}
          </Typography>
        )
      ) : (
        <>
          <Typography variant="body2" sx={{ mb: 1 }}>
            {t(filter === "keepers" ? "outreach.infographics.summaryKeepers" : "outreach.infographics.summaryAll", {
              total: list.length,
              usable: usableCount,
            })}
          </Typography>
          <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 1.5 }}>
            {visible.map((item, idx) => {
              const status = statusOf(item);
              const busy = busyId === item.document_id;
              const name = item.headline || t("outreach.infographics.untitled", { id: item.document_id });
              return (
                <Paper key={item.document_id} variant="outlined" sx={{ p: 1, display: "flex", flexDirection: "column", gap: 0.75 }}>
                  <Box
                    component="button"
                    type="button"
                    onClick={() => setLightboxIndex(idx)}
                    aria-label={t("outreach.infographics.enlarge", { name })}
                    sx={{
                      p: 0, border: 0, borderRadius: 1, overflow: "hidden", cursor: "zoom-in", bgcolor: "grey.900",
                      // Holds the tile's height before the image arrives, so lazy loading can tell what is on screen.
                      aspectRatio: item.width && item.height ? `${item.width} / ${item.height}` : "9 / 16",
                      maxHeight: 360,
                    }}
                  >
                    <img
                      src={guardedMediaSrc(outreachMediaUrl(item.image_url))}
                      alt={name}
                      loading="lazy"
                      style={{ width: "100%", height: "100%", objectFit: "contain", display: "block" }}
                    />
                  </Box>
                  <Typography variant="body2" sx={{ fontWeight: 600, wordBreak: "break-word" }}>
                    {name}
                  </Typography>
                  <FormControlLabel
                    sx={{ m: 0 }}
                    control={
                      <Switch
                        size="small"
                        checked={Boolean(item.keeper)}
                        disabled={busy}
                        onChange={(e) => act(item, () => setInfographicKeeper(item.document_id, e.target.checked))}
                      />
                    }
                    label={<Typography variant="caption">{t("outreach.infographics.keeper")}</Typography>}
                  />
                  <Readback item={item} />
                  {item.keeper && (
                    <Typography variant="caption" color={item.usable ? "text.primary" : "warning.main"}>
                      {t(
                        item.usable
                          ? "outreach.infographics.usable"
                          : status === "passed"
                            ? "outreach.infographics.notUsableOther"
                            : "outreach.infographics.notUsable",
                      )}
                    </Typography>
                  )}
                  <Box sx={{ flex: 1 }} />
                  <Button
                    size="small"
                    sx={{ alignSelf: "flex-start" }}
                    disabled={busy || status === "reading"}
                    onClick={() => act(item, () => rereadInfographic(item.document_id))}
                  >
                    {t(status === "unread" ? "outreach.infographics.readNow" : "outreach.infographics.readAgain")}
                  </Button>
                </Paper>
              );
            })}
          </Box>
          {list.length > visible.length && (
            <Stack direction="row" spacing={1} sx={{ alignItems: "center", mt: 1.5 }}>
              <Button size="small" variant="outlined" onClick={() => setShown((n) => n + PAGE_SIZE)}>
                {t("outreach.infographics.showMore")}
              </Button>
              <Typography variant="caption" color="text.secondary">
                {t("outreach.infographics.showing", { shown: visible.length, total: list.length })}
              </Typography>
            </Stack>
          )}
        </>
      )}

      {error && (
        <Alert severity="error" sx={{ mt: 1 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {lightboxItem && (
        <ImageLightbox
          imageUrl={outreachMediaUrl(lightboxItem.image_url)}
          imageName={lightboxItem.headline || t("outreach.infographics.untitled", { id: lightboxItem.document_id })}
          caption={(lightboxItem.lines || []).join(" · ")}
          onClose={() => setLightboxIndex(null)}
          onPrev={() => setLightboxIndex((i) => Math.max(0, i - 1))}
          onNext={() => setLightboxIndex((i) => Math.min(visible.length - 1, i + 1))}
          hasPrev={lightboxIndex > 0}
          hasNext={lightboxIndex < visible.length - 1}
        />
      )}
    </Paper>
  );
};

export default InfographicKeepersCard;
