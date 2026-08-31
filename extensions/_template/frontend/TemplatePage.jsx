import React, { useEffect, useState } from "react";
import { Typography } from "@mui/material";
import PageLayout from "@/components/layout/PageLayout";
import { BASE_URL, handleResponse } from "@/api/apiClient";

// Core is imported through the `@` alias; relative paths would tie the
// extension to where core happens to sit on disk.
const TemplatePage = () => {
  const [pong, setPong] = useState(null);
  useEffect(() => {
    fetch(`${BASE_URL}/template/ping`)
      .then(handleResponse)
      .then((d) => setPong(Boolean((d.data || d).pong)))
      .catch(() => setPong(false));
  }, []);
  return (
    <PageLayout title="Template">
      <Typography variant="body2">Backend says: {pong === null ? "…" : pong ? "pong" : "no answer"}</Typography>
    </PageLayout>
  );
};

export default TemplatePage;
