// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from Dean (user/owner).
// Approved: Logo branding rollout per Dean's request.
import React, { useEffect } from "react";
import { Box, Typography } from "@mui/material";
import { GuaardvarkLogo } from "../components/branding";
import { useLayout } from "../contexts/LayoutContext";
import PageLayout from "../components/layout/PageLayout";

const NotFoundPage = () => {
  const { setShowFooter } = useLayout();

  useEffect(() => {
    setShowFooter(false);
    return () => setShowFooter(true);
  }, [setShowFooter]);

  return (
    <PageLayout title="Page Not Found" variant="standard">
      <Box sx={{ display: "flex", flexDirection: "column", alignItems: "center", pt: 4 }}>
        <GuaardvarkLogo size={80} variant="warning" sx={{ mb: 2 }} />
        <Typography variant="h5" component="h1" gutterBottom>
          404 - Page Not Found
        </Typography>
      </Box>
    </PageLayout>
  );
};

export default NotFoundPage;
