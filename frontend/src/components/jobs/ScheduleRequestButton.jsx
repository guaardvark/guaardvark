// frontend/src/components/jobs/ScheduleRequestButton.jsx
//
// The schedule button for a page whose start is one request: `job()` returns
// what the page would send now ({ type, variant, name, request, path }, the
// kinds in backend/services/job_handoff.py), or a string saying why it cannot
// be scheduled yet. The job is saved for the night shift or a time and the
// page says so.
import React from "react";
import PropTypes from "prop-types";
import ScheduleButton from "./ScheduleButton";
import { createScheduledBatch, WHEN } from "../../api/nightShiftService";
import { useSnackbar } from "../common/SnackbarProvider";
import { uiLocale, useUiTranslation } from "../../i18n";

const ScheduleRequestButton = ({ job, disabled = false, tooltip = "", onScheduled }) => {
  const { t } = useUiTranslation();
  const toast = useSnackbar();

  const schedule = async (choice) => {
    const built = await job();
    if (!built) return;
    if (typeof built === "string") {
      toast?.showMessage(built, "warning");
      return;
    }
    try {
      const saved = await createScheduledBatch(built, choice);
      toast?.showMessage(
        choice.when === WHEN.AT
          ? t("jobs.nightShift.addedAt", { time: new Date(choice.at).toLocaleString(uiLocale()) })
          : t("jobs.nightShift.added"),
        "success",
      );
      onScheduled?.(saved);
    } catch (e) {
      toast?.showMessage(t("jobs.nightShift.addFailed", { error: e?.message || String(e) }), "error");
    }
  };

  return <ScheduleButton onSchedule={schedule} disabled={disabled} tooltip={tooltip} />;
};

ScheduleRequestButton.propTypes = {
  job: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  tooltip: PropTypes.string,
  onScheduled: PropTypes.func,
};

export default ScheduleRequestButton;
