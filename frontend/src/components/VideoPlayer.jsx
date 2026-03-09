// frontend/src/components/VideoPlayer.jsx
// Simple component to render local and remote video streams.
import React from "react";

const VideoPlayer = ({
  myVideo,
  userVideo,
  callAccepted,
  callEnded,
  stream,
}) => (
  <div
    style={{
      display: "flex",
      justifyContent: "center",
      gap: "1rem",
      padding: "1rem",
    }}
  >
    {stream && (
      <video
        playsInline
        muted
        ref={myVideo}
        autoPlay
        style={{ width: "300px" }}
      />
    )}
    {callAccepted && !callEnded && (
      <video playsInline ref={userVideo} autoPlay style={{ width: "300px" }} />
    )}
  </div>
);

export default VideoPlayer;
