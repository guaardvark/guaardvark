// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from the project maintainer.
// Approved: Logo branding rollout per maintainer request.
import React, { useEffect } from "react";
import { Box, Typography } from "@mui/material";
import { BrandLogo } from "../components/branding";
import { useLayout } from "../contexts/LayoutContext";
import PageLayout from "../components/layout/PageLayout";
import { useUiTranslation } from "../i18n";

const NotFoundPage = () => {
  const { t } = useUiTranslation();
  const { setShowFooter } = useLayout();

  useEffect(() => {
    setShowFooter(false);
    return () => setShowFooter(true);
  }, [setShowFooter]);

  return (
    <PageLayout title={t("notFound.title")} variant="standard">
      <Box sx={{ display: "flex", flexDirection: "column", alignItems: "center", pt: 4 }}>
        <BrandLogo size={80} variant="warning" sx={{ mb: 2 }} />
        <Typography variant="h5" component="h1" gutterBottom>
          {t("notFound.heading")}
        </Typography>
      </Box>
    </PageLayout>
  );
};

export default NotFoundPage;
