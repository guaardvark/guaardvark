import React, { useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
  Card,
  CardActionArea,
  CardContent,
} from "@mui/material";
import { setProfile as saveProfile } from "../../api/settingsService";
import { useAppStore } from "../../stores/useAppStore";
import { translate, useUiTranslation, Trans } from "../../i18n";

/**
 * Asked once, on a fresh install: what is Guaardvark for here?
 *
 * Either answer is a starting point, not a ceiling — everything stays one
 * setting away in Settings → Product Profile. Workstation is today's product
 * exactly; Creator needs a restart to take effect because feature flags are
 * read at boot. Distributions never see this: their installer names the
 * profile in .env.
 */
const choices = () => [
  {
    name: "creator",
    title: translate("dialogs.firstRunProfile.creatorTitle"),
    blurb: translate("dialogs.firstRunProfile.creatorBlurb"),
  },
  {
    name: "workstation",
    title: translate("dialogs.firstRunProfile.workstationTitle"),
    blurb: translate("dialogs.firstRunProfile.workstationBlurb"),
  },
];

const FirstRunProfileDialog = () => {
  const { t } = useUiTranslation();
  const pending = useAppStore((s) => s.profileFirstRun);
  const setPending = useAppStore((s) => s.setProfileFirstRun);
  const active = useAppStore((s) => s.profile);
  const [choice, setChoice] = useState("creator");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [restart, setRestart] = useState(false);

  if (!pending) return null;

  const confirm = async () => {
    setSaving(true);
    setError(null);
    try {
      const result = await saveProfile(choice);
      const needsRestart = Boolean((result?.data || result)?.restart_required);
      if (needsRestart) {
        setRestart(true);
      } else {
        setPending(false);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open maxWidth="sm" fullWidth aria-labelledby="first-run-profile-title">
      <DialogTitle id="first-run-profile-title">{t("dialogs.firstRunProfile.title")}</DialogTitle>
      <DialogContent dividers>
        {restart ? (
          <Alert severity="success">
            <Trans
              i18nKey="dialogs.firstRunProfile.restartNeeded"
              values={{ choice, profile: active?.label || active?.name }}
              components={{ bold: <strong /> }}
            />
          </Alert>
        ) : (
          <Stack spacing={1.5}>
            {choices().map((c) => (
              <Card
                key={c.name}
                variant="outlined"
                sx={{ borderColor: choice === c.name ? "primary.main" : "divider" }}
              >
                <CardActionArea onClick={() => setChoice(c.name)} disabled={saving}>
                  <CardContent>
                    <Typography variant="subtitle1">{c.title}</Typography>
                    <Typography variant="body2" color="text.secondary">
                      {c.blurb}
                    </Typography>
                  </CardContent>
                </CardActionArea>
              </Card>
            ))}
            <Typography variant="caption" color="text.secondary">
              {t("dialogs.firstRunProfile.changeLater")}
            </Typography>
            {error && (
              <Alert severity="warning">
                <Trans
                  i18nKey="dialogs.firstRunProfile.saveFailed"
                  values={{ error, choice }}
                  components={{ code: <code /> }}
                />
              </Alert>
            )}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        {restart ? (
          <Button onClick={() => setPending(false)} variant="contained">
            {t("dialogs.firstRunProfile.gotIt")}
          </Button>
        ) : (
          <>
            {error && (
              <Button onClick={() => setPending(false)} disabled={saving}>
                {t("dialogs.firstRunProfile.later")}
              </Button>
            )}
            <Button onClick={confirm} variant="contained" disabled={saving}>
              {saving ? t("dialogs.firstRunProfile.saving") : t("dialogs.firstRunProfile.continue")}
            </Button>
          </>
        )}
      </DialogActions>
    </Dialog>
  );
};

export default FirstRunProfileDialog;
