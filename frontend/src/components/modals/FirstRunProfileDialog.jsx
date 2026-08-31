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

/**
 * Asked once, on a fresh install: what is Guaardvark for here?
 *
 * Either answer is a starting point, not a ceiling — everything stays one
 * setting away in Settings → Product Profile. Workstation is today's product
 * exactly; Creator needs a restart to take effect because feature flags are
 * read at boot. Distributions never see this: their installer names the
 * profile in .env.
 */
const CHOICES = [
  {
    name: "creator",
    title: "Creator",
    blurb: "Image, video, audio and Film Crew. Agents, the knowledge index, outreach and automation stay installed but out of the way.",
  },
  {
    name: "workstation",
    title: "Workstation",
    blurb: "Everything: chat, knowledge index, agents, media studio, outreach, automation.",
  },
];

const FirstRunProfileDialog = () => {
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
      <DialogTitle id="first-run-profile-title">What do you want Guaardvark for?</DialogTitle>
      <DialogContent dividers>
        {restart ? (
          <Alert severity="success">
            Profile set to <strong>{choice}</strong>. Restart Guaardvark to apply it; until then you are
            on <strong>{active?.label || active?.name}</strong>.
          </Alert>
        ) : (
          <Stack spacing={1.5}>
            {CHOICES.map((c) => (
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
              A starting point, not a ceiling — change it any time in Settings → Product Profile.
            </Typography>
            {error && (
              <Alert severity="warning">
                Could not save the choice ({error}). Set <code>GUAARDVARK_PROFILE={choice}</code> in{" "}
                <code>.env</code>, or run <code>./start.sh --profile {choice}</code>.
              </Alert>
            )}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        {restart ? (
          <Button onClick={() => setPending(false)} variant="contained">
            Got it
          </Button>
        ) : (
          <>
            {error && (
              <Button onClick={() => setPending(false)} disabled={saving}>
                Later
              </Button>
            )}
            <Button onClick={confirm} variant="contained" disabled={saving}>
              {saving ? "Saving..." : "Continue"}
            </Button>
          </>
        )}
      </DialogActions>
    </Dialog>
  );
};

export default FirstRunProfileDialog;
