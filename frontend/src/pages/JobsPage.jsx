// frontend/src/pages/JobsPage.jsx
//
// User-initiated work: scheduled tasks, batch CSV jobs, video editor renders,
// and any other queue that the user added items to. Counterpart to
// ActivityPage which shows system-driven work (training, self-improvement,
// research, etc.). Both share JobsList; only the kind filter differs.
import React from "react";
import PageLayout from "../components/layout/PageLayout";
import JobsList from "../components/jobs/JobsList";
import { JOB_KINDS_FOR_JOBS_PAGE } from "../api/jobsService";

const JobsPage = () => (
  <PageLayout title="Jobs" subtitle="User-initiated work and queues">
    <JobsList
      title="Jobs"
      subtitle="Things you started — scheduled tasks, batch generations, editor renders"
      kinds={JOB_KINDS_FOR_JOBS_PAGE}
    />
  </PageLayout>
);

export default JobsPage;
