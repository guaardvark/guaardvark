// frontend/src/pages/ActivityPage.jsx
//
// System-driven work: indexing, agent runs, training, self-improvement,
// research, demonstrations, and any other process the system runs on
// its own. Counterpart to JobsPage which shows user-initiated work.
// Both share JobsList; only the kind filter differs.
import React from "react";
import PageLayout from "../components/layout/PageLayout";
import JobsList from "../components/jobs/JobsList";
import { JOB_KINDS_FOR_ACTIVITY_PAGE } from "../api/jobsService";

const ActivityPage = () => (
  <PageLayout title="Activity" subtitle="What the system is doing on its own">
    <JobsList kinds={JOB_KINDS_FOR_ACTIVITY_PAGE} />
  </PageLayout>
);

export default ActivityPage;
