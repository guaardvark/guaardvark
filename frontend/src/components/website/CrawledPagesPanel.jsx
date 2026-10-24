// frontend/src/components/website/CrawledPagesPanel.jsx
// Lists the pages persisted by a website crawl (the "Pages" tab). Reads
// GET /api/websites/:id/pages; opening a row fetches full content.
import React, { useCallback, useEffect, useState } from "react";
import {
  Box,
  Chip,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  FormControl,
  IconButton,
  MenuItem,
  Paper,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import { getWebsitePages, getWebsitePage } from "../../api/websiteService";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

const statusLabels = () => ({
  crawled: translate("websites.crawledPages.status.crawled"),
  error: translate("websites.crawledPages.status.error"),
});

const formatTime = (iso) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(uiLocale());
  } catch {
    return iso;
  }
};

const CrawledPagesPanel = ({ website, onFeedback }) => {
  const { t } = useUiTranslation();
  const websiteId = website?.id;

  const [pages, setPages] = useState([]);
  const [total, setTotal] = useState(0);
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const loadPages = useCallback(async () => {
    if (!websiteId) return;
    setLoading(true);
    try {
      const params = { limit: 500 };
      if (statusFilter) params.status = statusFilter;
      const data = await getWebsitePages(websiteId, params);
      setPages(Array.isArray(data?.pages) ? data.pages : []);
      setTotal(data?.total ?? 0);
    } catch (err) {
      onFeedback?.({
        open: true,
        message: t("websites.crawledPages.loadFailed", {
          error: err.message || t("websites.crawledPages.unknownError"),
        }),
        severity: "error",
      });
    } finally {
      setLoading(false);
    }
  }, [websiteId, statusFilter, onFeedback]);

  useEffect(() => {
    loadPages();
  }, [loadPages]);

  const openPage = async (row) => {
    setSelected({ ...row });
    setDetailLoading(true);
    try {
      const full = await getWebsitePage(websiteId, row.id);
      setSelected(full);
    } catch {
      // keep the summary row we already have
    } finally {
      setDetailLoading(false);
    }
  };

  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="subtitle2" sx={{ flexGrow: 1 }}>
          {t("websites.crawledPages.title")}{" "}
          {total ? `(${total})` : ""}
        </Typography>
        <FormControl size="small" sx={{ minWidth: 130 }}>
          <Select
            value={statusFilter}
            displayEmpty
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <MenuItem value="">{t("websites.crawledPages.filterAll")}</MenuItem>
            <MenuItem value="crawled">{t("websites.crawledPages.filterCrawled")}</MenuItem>
            <MenuItem value="error">{t("websites.crawledPages.filterError")}</MenuItem>
          </Select>
        </FormControl>
        <Tooltip title={t("websites.crawledPages.refresh")}>
          <span>
            <IconButton size="small" onClick={loadPages} disabled={loading}>
              <RefreshIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Stack>

      <Paper variant="outlined" sx={{ overflow: "hidden" }}>
        <TableContainer sx={{ maxHeight: 480 }}>
          <Table stickyHeader size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ fontWeight: "bold" }}>{t("websites.crawledPages.columns.title")}</TableCell>
                <TableCell sx={{ fontWeight: "bold" }}>{t("websites.crawledPages.columns.url")}</TableCell>
                <TableCell sx={{ fontWeight: "bold" }}>{t("websites.crawledPages.columns.status")}</TableCell>
                <TableCell sx={{ fontWeight: "bold" }}>{t("websites.crawledPages.columns.crawled")}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {pages.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={4}>
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ py: 2, textAlign: "center", fontStyle: "italic" }}
                    >
                      {loading
                        ? t("websites.crawledPages.loading")
                        : t("websites.crawledPages.empty")}
                    </Typography>
                  </TableCell>
                </TableRow>
              ) : (
                pages.map((row) => (
                  <TableRow key={row.id} hover sx={{ cursor: "pointer" }} onClick={() => openPage(row)}>
                    <TableCell
                      sx={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                    >
                      {row.title || "—"}
                    </TableCell>
                    <TableCell
                      sx={{ maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                    >
                      <Tooltip title={row.error_message ? `${row.url}\n\n${row.error_message}` : row.url}>
                        <Typography variant="body2">{row.url}</Typography>
                      </Tooltip>
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={statusLabels()[row.status] || row.status}
                        color={row.status === "error" ? "error" : "success"}
                        size="small"
                        sx={{ textTransform: "capitalize" }}
                      />
                    </TableCell>
                    <TableCell>{formatTime(row.crawled_at)}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      <Dialog open={!!selected} onClose={() => setSelected(null)} fullWidth maxWidth="md">
        {selected && (
          <>
            <DialogTitle sx={{ pb: 0 }}>
              {selected.title || t("websites.crawledPages.pageFallback")}
              <Typography variant="body2" color="text.secondary" sx={{ wordBreak: "break-all" }}>
                <a href={selected.url} target="_blank" rel="noreferrer">
                  {selected.url}
                </a>
              </Typography>
            </DialogTitle>
            <DialogContent dividers>
              {detailLoading ? (
                <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
                  <CircularProgress />
                </Box>
              ) : selected.status === "error" ? (
                <Typography color="error" variant="body2">
                  {selected.error_message || t("websites.crawledPages.crawlError")}
                </Typography>
              ) : (
                <>
                  {selected.meta_description && (
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                      {selected.meta_description}
                    </Typography>
                  )}
                  <Typography
                    variant="body2"
                    component="pre"
                    sx={{ whiteSpace: "pre-wrap", fontFamily: "inherit", m: 0 }}
                  >
                    {selected.content || t("websites.crawledPages.noContent")}
                  </Typography>
                </>
              )}
            </DialogContent>
          </>
        )}
      </Dialog>
    </Box>
  );
};

export default CrawledPagesPanel;
