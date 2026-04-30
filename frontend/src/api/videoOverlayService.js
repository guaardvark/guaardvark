// Thin wrapper around POST /api/video-overlay/text. The backend does all
// the ffmpeg drawtext work; this just hands params over and returns the
// new Document so the UI can preview/download it.
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export const overlayText = async ({
  documentId,
  text,
  fontSize = 48,
  fontColor = "white",
  position = "bottom-center",
  border = true,
  borderWidth = 2,
  borderColor = "black",
  boxBackground = false,
  boxColor = "black@0.5",
  boxBorderWidth = 10,
}) => {
  const res = await axios.post(`${API_BASE}/video-overlay/text`, {
    document_id: documentId,
    text,
    font_size: fontSize,
    font_color: fontColor,
    position,
    border,
    border_width: borderWidth,
    border_color: borderColor,
    box_background: boxBackground,
    box_color: boxColor,
    box_border_width: boxBorderWidth,
  });
  return res.data?.data || res.data;  // success_response wraps in {data, message, status}
};

// List existing video Documents so the user can pick one to overlay text on.
// Backed by the dedicated /api/video-overlay/videos endpoint — the generic
// /files/search requires a non-empty query so we'd need to pick an arbitrary
// substring there to populate a "pick a video" dropdown.
export const listVideoDocuments = async () => {
  const res = await axios.get(`${API_BASE}/video-overlay/videos?limit=200`);
  return res.data?.data?.videos || res.data?.videos || [];
};

// Phase 6 of the editor plan — list audio Documents for the timeline's
// audio rail. Same pattern as listVideoDocuments.
export const listAudioDocuments = async () => {
  const res = await axios.get(`${API_BASE}/video-overlay/audio-library?limit=200`);
  return res.data?.data?.audio || res.data?.audio || [];
};

// Mirror of the above for image documents — populates the editor's
// Images tab in the media library.
export const listImageDocuments = async () => {
  const res = await axios.get(`${API_BASE}/video-overlay/image-library?limit=200`);
  return res.data?.data?.images || res.data?.images || [];
};

// Phase 7 — render a Video Editor timeline to a final mp4.
export const renderTimeline = async (timeline) => {
  // timeline shape mirrors the backend endpoint's expected payload.
  const res = await axios.post(`${API_BASE}/video-overlay/render-timeline`, timeline, {
    timeout: 600000,  // 10 min — large renders happen.
  });
  return res.data?.data || res.data;
};
