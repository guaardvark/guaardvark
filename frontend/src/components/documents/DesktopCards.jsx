// What the Thumbnails and List views show of a file or folder: a picture of
// it, a title and a line about it. The Files and Media pages draw the same.

import React from "react";
import { Box, Typography } from "@mui/material";
import {
  FolderOutlined,
  InsertDriveFileOutlined,
  MusicNote,
  PlayArrow,
} from "@mui/icons-material";
import { useUiTranslation } from "../../i18n";
import { kindOf, thumbnailUrl } from "./desktopView";
import { formatBytes, formatDate } from "./fileUtils.jsx";

export const formatDuration = (seconds) => {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) return null;
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

const modelName = (model) => (model ? String(model).split("/").pop() : null);

/** A file's picture at a fixed height: its thumbnail, or an icon for a sound or another file. */
export const FileThumb = React.memo(({ file, height, size }) => {
  const kind = kindOf(file);
  const duration = formatDuration(file.duration_s);
  const badge = (text) => (
    <Box
      sx={{
        position: "absolute", right: 4, bottom: 4, px: 0.5, borderRadius: 0.5,
        bgcolor: "rgba(0,0,0,0.75)", color: "#fff", fontSize: "0.7rem", lineHeight: 1.5,
        display: "flex", alignItems: "center", gap: 0.25,
      }}
    >
      {text}
    </Box>
  );
  if (kind === "image" || kind === "video") {
    return (
      <Box sx={{ position: "relative", height, bgcolor: "action.hover", borderRadius: 1, overflow: "hidden" }}>
        <Box
          component="img"
          src={thumbnailUrl(file, size)}
          alt=""
          loading="lazy"
          decoding="async"
          draggable={false}
          onError={(e) => { e.currentTarget.style.visibility = "hidden"; }}
          sx={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
        />
        {kind === "video" && badge(<><PlayArrow sx={{ fontSize: 12 }} />{duration}</>)}
      </Box>
    );
  }
  const Icon = kind === "audio" ? MusicNote : InsertDriveFileOutlined;
  return (
    <Box
      sx={{
        position: "relative", height, borderRadius: 1, display: "flex", alignItems: "center",
        justifyContent: "center", bgcolor: kind === "audio" ? "rgba(156, 39, 176, 0.12)" : "action.hover",
      }}
    >
      <Icon sx={{ fontSize: Math.min(64, height * 0.5), color: kind === "audio" ? "secondary.main" : "text.secondary" }} />
      {duration && badge(duration)}
    </Box>
  );
});
FileThumb.displayName = "FileThumb";

/** One line about a file: what it is, what made it and when. */
export const useFileMetaLine = () => {
  const { t } = useUiTranslation();
  return (file) => [
    t(`documents.page.kindLabel.${kindOf(file)}`),
    modelName(file.model),
    formatDate(file.updated_at || file.uploaded_at),
  ].filter(Boolean).join(" · ");
};

/** The inside of a file card in the Thumbnails view. */
export const ThumbFileBody = ({ file, metaLine }) => (
  <Box sx={{ textAlign: "left" }}>
    <FileThumb file={file} height={135} size={480} />
    <Typography
      variant="body2"
      title={file.prompt || file.filename}
      sx={{
        mt: 0.75, fontWeight: 500, lineHeight: 1.3, display: "-webkit-box", WebkitLineClamp: 2,
        WebkitBoxOrient: "vertical", overflow: "hidden", wordBreak: "break-word",
      }}
    >
      {file.title || file.filename}
    </Typography>
    <Typography variant="caption" color="text.secondary" noWrap sx={{ display: "block" }}>
      {metaLine(file)}
    </Typography>
  </Box>
);

/** The inside of a folder card in the Thumbnails view. */
export const ThumbFolderBody = ({ folder, color, itemCountLabel }) => (
  <Box sx={{ textAlign: "left" }}>
    <Box
      sx={{
        height: 135, borderRadius: 1, bgcolor: "action.hover", display: "flex",
        alignItems: "center", justifyContent: "center",
      }}
    >
      <FolderOutlined sx={{ fontSize: 88, color }} />
    </Box>
    <Typography variant="body2" noWrap title={folder.name} sx={{ mt: 0.75, fontWeight: 500 }}>
      {folder.name}
    </Typography>
    <Typography variant="caption" color="text.secondary" noWrap sx={{ display: "block" }}>
      {itemCountLabel}
    </Typography>
  </Box>
);

/** The cells of a file row in the List view after its picture. */
export const ListFileCells = ({ file, kindLabel }) => (
  <>
    <Box sx={{ flex: 1, minWidth: 0 }}>
      <Typography variant="body2" noWrap title={file.prompt || file.filename}>
        {file.title || file.filename}
      </Typography>
      {file.title && file.title !== file.filename && (
        <Typography variant="caption" color="text.secondary" noWrap sx={{ display: "block" }}>
          {file.filename}
        </Typography>
      )}
    </Box>
    <Typography variant="caption" color="text.secondary" noWrap sx={{ width: 110, flexShrink: 0 }}>
      {[kindLabel, formatDuration(file.duration_s)].filter(Boolean).join(" · ")}
    </Typography>
    <Typography variant="caption" color="text.secondary" sx={{ width: 150, flexShrink: 0, textAlign: "right" }}>
      {formatDate(file.updated_at || file.uploaded_at)}
    </Typography>
    <Typography variant="caption" color="text.secondary" sx={{ width: 72, flexShrink: 0, textAlign: "right" }}>
      {file.size ? formatBytes(file.size) : ""}
    </Typography>
  </>
);
