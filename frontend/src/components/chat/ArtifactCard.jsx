/**
 * ArtifactCard - Inline presentation of a file a tool wrote during chat.
 * Shows the file header (icon, name, type, size), copy/download/open actions,
 * and a body rendered by file type: table for CSV, markdown, highlighted code,
 * or plain preformatted text.
 */
import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Typography,
  Chip,
  IconButton,
  Tooltip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
} from "@mui/material";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import CheckIcon from "@mui/icons-material/Check";
import DownloadIcon from "@mui/icons-material/Download";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import TableChartIcon from "@mui/icons-material/TableChart";
import ArticleIcon from "@mui/icons-material/Article";
import DataObjectIcon from "@mui/icons-material/DataObject";
import CodeIcon from "@mui/icons-material/Code";
import TerminalIcon from "@mui/icons-material/Terminal";
import DescriptionIcon from "@mui/icons-material/Description";
import InsertDriveFileIcon from "@mui/icons-material/InsertDriveFile";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { a11yDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { BASE_URL } from "../../api/apiClient";
import { guardedMediaSrc } from "../../utils/assetGuard";

// Body height: ~24 lines of 0.7rem code, small enough that one card never
// fills the chat viewport (the floating chat card is ~600px tall).
const BODY_MAX_HEIGHT = 420;

// Matches the copied-state reset used by CodeViewerModal.
const COPIED_RESET_MS = 2000;

const MARKDOWN_TYPES = new Set(["md", "markdown"]);

// file_type (lowercase extension) -> Prism language name.
const CODE_LANGUAGES = {
  json: "json",
  py: "python",
  js: "javascript",
  jsx: "jsx",
  ts: "typescript",
  tsx: "tsx",
  html: "markup",
  css: "css",
  sh: "bash",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  sql: "sql",
};

const fileIconFor = (fileType) => {
  const sx = { fontSize: 16, color: "text.secondary" };
  if (fileType === "csv") return <TableChartIcon sx={sx} />;
  if (MARKDOWN_TYPES.has(fileType)) return <ArticleIcon sx={sx} />;
  if (fileType === "json") return <DataObjectIcon sx={sx} />;
  if (fileType === "sh") return <TerminalIcon sx={sx} />;
  if (CODE_LANGUAGES[fileType]) return <CodeIcon sx={sx} />;
  if (fileType === "txt") return <DescriptionIcon sx={sx} />;
  return <InsertDriveFileIcon sx={sx} />;
};

export const formatBytes = (bytes) => {
  if (bytes == null || Number.isNaN(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
};

// The backend reports "/api/outputs/<path>"; when the client talks to a
// different API root (VITE_API_BASE_URL), swap the prefix for that root.
export const resolveArtifactUrl = (url) => {
  if (!url) return null;
  if (url.startsWith("/api/") && BASE_URL !== "/api") {
    return `${BASE_URL}${url.slice("/api".length)}`;
  }
  return url;
};

/**
 * Renders a comma-separated string as a compact table. Returns null when the
 * input has no commas, so callers can fall back to plain text.
 */
export const CSVTable = ({ csvString, maxHeight = 200 }) => {
  if (!csvString || !csvString.includes(",")) return null;
  const lines = csvString.trim().split("\n");
  if (lines.length < 1) return null;

  const headers = lines[0].split(",").map(h => h.trim());
  const rows = lines.slice(1).map(line => line.split(",").map(c => c.trim()));

  return (
    <TableContainer component={Paper} variant="outlined" sx={{ my: 0.5, maxHeight, overflow: "auto" }}>
      <Table size="small" stickyHeader>
        <TableHead>
          <TableRow sx={{ bgcolor: "action.hover" }}>
            {headers.map((h, i) => (
              <TableCell key={i} sx={{ py: 0.25, px: 0.5, fontSize: "0.6rem", fontWeight: "bold" }}>{h}</TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row, i) => (
            <TableRow key={i}>
              {row.map((cell, j) => (
                <TableCell key={j} sx={{ py: 0.25, px: 0.5, fontSize: "0.6rem" }}>{cell}</TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
};

CSVTable.propTypes = {
  csvString: PropTypes.string,
  maxHeight: PropTypes.number,
};

const PlainText = ({ text }) => (
  <Box
    component="pre"
    sx={{
      m: 0,
      p: 1,
      bgcolor: "background.default",
      color: "text.primary",
      borderRadius: 0.5,
      fontSize: "0.7rem",
      fontFamily: "monospace",
      whiteSpace: "pre-wrap",
      wordBreak: "break-word",
    }}
  >
    {text}
  </Box>
);

PlainText.propTypes = { text: PropTypes.string };

const MarkdownBody = ({ text }) => (
  <Box
    sx={{
      px: 1,
      fontSize: "0.8rem",
      "& p": { my: 0.5 },
      "& pre": { overflowX: "auto", borderRadius: 1, fontSize: "0.7rem" },
      "& table": { borderCollapse: "collapse", fontSize: "0.75rem" },
      "& th, & td": { border: "1px solid", borderColor: "divider", px: 0.75, py: 0.25 },
      "& img": { maxWidth: "100%", display: "block", borderRadius: "4px" },
    }}
  >
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        // Offline-first: never load a remote image URL from generated markdown.
        img: (props) => <img {...props} src={guardedMediaSrc(props.src)} alt={props.alt || ""} />,
        code({ inline, className, children, ...props }) {
          const match = /language-(\w+)/.exec(className || "");
          return !inline && match ? (
            <SyntaxHighlighter style={a11yDark} language={match[1]} PreTag="div" {...props}>
              {String(children).replace(/\n$/, "")}
            </SyntaxHighlighter>
          ) : (
            <code className={className} {...props}>
              {children}
            </code>
          );
        },
      }}
    >
      {text}
    </ReactMarkdown>
  </Box>
);

MarkdownBody.propTypes = { text: PropTypes.string };

const ArtifactBody = ({ fileType, content }) => {
  if (fileType === "csv") {
    const table = <CSVTable csvString={content} maxHeight={BODY_MAX_HEIGHT} />;
    return content.includes(",") ? table : <PlainText text={content} />;
  }
  if (MARKDOWN_TYPES.has(fileType)) return <MarkdownBody text={content} />;
  const language = CODE_LANGUAGES[fileType];
  if (language) {
    return (
      <SyntaxHighlighter
        language={language}
        style={a11yDark}
        customStyle={{ fontSize: "0.7rem", margin: 0, borderRadius: 4 }}
        wrapLongLines
      >
        {content}
      </SyntaxHighlighter>
    );
  }
  return <PlainText text={content} />;
};

ArtifactBody.propTypes = {
  fileType: PropTypes.string,
  content: PropTypes.string,
};

const ArtifactCard = ({ artifact }) => {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return undefined;
    const timer = setTimeout(() => setCopied(false), COPIED_RESET_MS);
    return () => clearTimeout(timer);
  }, [copied]);

  if (!artifact) return null;

  const fileType = (artifact.file_type || "").toLowerCase();
  const hasContent = typeof artifact.content === "string";
  const url = resolveArtifactUrl(artifact.url);
  const inlineUrl = url ? `${url}${url.includes("?") ? "&" : "?"}inline=1` : null;
  const size = formatBytes(artifact.size_bytes);

  const handleCopy = async () => {
    if (!hasContent) return;
    try {
      await navigator.clipboard.writeText(artifact.content);
      setCopied(true);
    } catch (err) {
      console.error("Copy failed:", err);
    }
  };

  const note = !hasContent
    ? "Preview not available for this file; use Download or Open."
    : artifact.content_truncated
    ? "Preview truncated; download the file for the full content."
    : null;

  return (
    <Box
      data-testid="artifact-card"
      sx={{
        mt: 0.75,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        overflow: "hidden",
        maxWidth: "100%",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          px: 1,
          py: 0.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "action.hover",
          minWidth: 0,
        }}
      >
        {fileIconFor(fileType)}
        <Typography
          variant="caption"
          title={artifact.filename}
          sx={{
            fontFamily: "monospace",
            fontWeight: 600,
            flex: 1,
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {artifact.filename}
        </Typography>
        {fileType && (
          <Chip label={fileType} size="small" variant="outlined" sx={{ height: 18, fontSize: "0.6rem" }} />
        )}
        {size && (
          <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "nowrap" }}>
            {size}
          </Typography>
        )}
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
          {hasContent && (
            <Tooltip title={copied ? "Copied" : "Copy content"}>
              <IconButton size="small" aria-label="Copy content" onClick={handleCopy} sx={{ p: 0.25 }}>
                {copied ? (
                  <CheckIcon sx={{ fontSize: 16, color: "success.main" }} />
                ) : (
                  <ContentCopyIcon sx={{ fontSize: 16 }} />
                )}
              </IconButton>
            </Tooltip>
          )}
          {url && (
            <Tooltip title="Download">
              <IconButton
                size="small"
                aria-label="Download"
                component="a"
                href={url}
                download={artifact.filename}
                sx={{ p: 0.25 }}
              >
                <DownloadIcon sx={{ fontSize: 16 }} />
              </IconButton>
            </Tooltip>
          )}
          {inlineUrl && (
            <Tooltip title="Open in new tab">
              <IconButton
                size="small"
                aria-label="Open in new tab"
                component="a"
                href={inlineUrl}
                target="_blank"
                rel="noopener noreferrer"
                sx={{ p: 0.25 }}
              >
                <OpenInNewIcon sx={{ fontSize: 16 }} />
              </IconButton>
            </Tooltip>
          )}
        </Box>
      </Box>

      {note && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", px: 1, py: 0.5, fontStyle: "italic" }}
        >
          {note}
        </Typography>
      )}

      {hasContent && (
        <Box
          data-testid="artifact-body"
          sx={{ maxHeight: BODY_MAX_HEIGHT, overflow: "auto", maxWidth: "100%", p: 0.5 }}
        >
          <ArtifactBody fileType={fileType} content={artifact.content} />
        </Box>
      )}
    </Box>
  );
};

export const artifactShape = PropTypes.shape({
  filename: PropTypes.string.isRequired,
  file_type: PropTypes.string,
  size_bytes: PropTypes.number,
  url: PropTypes.string,
  content: PropTypes.string,
  content_truncated: PropTypes.bool,
});

ArtifactCard.propTypes = {
  artifact: artifactShape,
};

export default ArtifactCard;
