/**
 * SelfEditsList - the changes Guaardvark made to its own code, newest first,
 * each with its checks and, while it is in place, a Revert.
 */
import React, { useCallback, useEffect, useState } from "react";
import { Box, Typography } from "@mui/material";
import SelfEditNotice from "../chat/SelfEditNotice";
import { listSelfEdits } from "../../api/selfEditService";
import { useUiTranslation } from "../../i18n";

const SelfEditsList = () => {
  const { t } = useUiTranslation();
  const [edits, setEdits] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const data = await listSelfEdits(50);
      setEdits(data?.edits || []);
    } catch (e) {
      setError(e.message || String(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (error) return <Typography variant="body2" color="error">{error}</Typography>;
  if (edits === null) return null;
  if (edits.length === 0) {
    return <Typography variant="body2" color="text.secondary">{t("selfImprovement.selfEdits.none")}</Typography>;
  }
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      {edits.map((edit) => (
        <Box key={edit.id}>
          <Typography variant="caption" color="text.secondary">
            {t("selfImprovement.selfEdits.when", { when: (edit.created_at || "").replace("T", " "), id: edit.id })}
          </Typography>
          <SelfEditNotice
            edit={{ ...edit, files: (edit.files || []).map((f) => f.path) }}
          />
        </Box>
      ))}
    </Box>
  );
};

export default SelfEditsList;
