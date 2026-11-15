// Small helpers the Image Gen page and its cards share.

export const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

/** Recent Batches shows this many more each time. */
export const HISTORY_PAGE = 24;

export const encodeFilename = (filename) => {
  if (!filename) return '';
  return filename.split('/').map(part => encodeURIComponent(part)).join('/');
};

/** The status blocks were cards of their own; inside a card frame they are plain sections. */
export const statusSx = {
  p: 0.5,
  "& > .MuiCard-root": { boxShadow: "none", backgroundColor: "transparent", backgroundImage: "none", mt: 0, mb: 1 },
};
