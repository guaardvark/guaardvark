import React from "react";
import { Box, Typography } from "@mui/material";

/** Sampler thumbnail. Colourful blobs are expected (Latent2RGB), not the final MP4. */
export default function LiveLatentPreview({ src, aspectRatio = "16 / 9" }) {
  if (!src) return null;
  return (
    <Box sx={{ mb: 1.5 }}>
      <Box
        sx={{
          position: "relative",
          width: "100%",
          maxWidth: 360,
          aspectRatio,
          borderRadius: 1,
          overflow: "hidden",
          bgcolor: "grey.900",
        }}
      >
        <Box
          component="img"
          src={src}
          alt="Live latent preview"
          sx={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
        />
      </Box>
      <Typography variant="caption" color="text.secondary">
        Live latent preview — not final quality
      </Typography>
    </Box>
  );
}
