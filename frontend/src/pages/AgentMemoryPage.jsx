// frontend/src/pages/AgentMemoryPage.jsx
// Agent Memory page — the memory table that used to live only inside Settings.

import React from "react";
import PageLayout from "../components/layout/PageLayout";
import MemoryManagementSection from "../components/settings/MemoryManagementSection";
import { useUiTranslation } from "../i18n";

// The section is self-contained (it fetches /api/memory and owns its dialogs),
// so the page is only the chrome. title={null} suppresses the section's own
// heading — PageLayout's header already carries "Agent Memory".
const AgentMemoryPage = () => {
  const { t } = useUiTranslation();
  return (
    <PageLayout title={t("agents.memoryPage.title")} variant="standard">
      <MemoryManagementSection title={null} icon={null} />
    </PageLayout>
  );
};

export default AgentMemoryPage;
