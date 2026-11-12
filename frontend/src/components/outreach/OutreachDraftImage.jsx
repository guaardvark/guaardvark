// The infographic a draft carries: a thumbnail that opens larger, and a line
// saying it goes out with the post. Renders nothing for a draft without one.
import React, { useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import ImageLightbox from "../images/ImageLightbox";
import { outreachMediaUrl } from "../../api/outreachService";
import { guardedMediaSrc } from "../../utils/assetGuard";
import { useUiTranslation } from "../../i18n";

// Statuses in which the post has not gone out yet.
const BEFORE_POST = ["candidate", "drafted", "approved", "processing", "submitting"];

const OutreachDraftImage = ({ draft, sx }) => {
  const { t } = useUiTranslation();
  const [open, setOpen] = useState(false);
  if (!draft?.image_url) return null;

  const url = outreachMediaUrl(draft.image_url);
  const alt = draft.image_alt || t("outreach.draftImage.altFallback");
  const pending = !draft.status || BEFORE_POST.includes(draft.status);

  return (
    <Stack direction="row" spacing={1} alignItems="center" sx={sx}>
      <Box
        component="button"
        type="button"
        onClick={() => setOpen(true)}
        aria-label={t("outreach.draftImage.enlarge")}
        title={t("outreach.draftImage.enlarge")}
        sx={{
          p: 0, border: 1, borderColor: "divider", borderRadius: 1, overflow: "hidden",
          cursor: "zoom-in", bgcolor: "grey.900", flexShrink: 0, lineHeight: 0,
        }}
      >
        <img
          src={guardedMediaSrc(url)}
          alt={alt}
          loading="lazy"
          style={{ display: "block", maxWidth: 96, maxHeight: 128, objectFit: "contain" }}
        />
      </Box>
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="body2">
          {t(pending ? "outreach.draftImage.willAttach" : "outreach.draftImage.chosen")}
        </Typography>
        {draft.image_alt && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
            {t("outreach.draftImage.altLine", { alt: draft.image_alt })}
          </Typography>
        )}
      </Box>
      {open && <ImageLightbox imageUrl={url} imageName={alt} onClose={() => setOpen(false)} />}
    </Stack>
  );
};

export default OutreachDraftImage;
