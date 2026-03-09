import React, { useState, useEffect } from "react";
import { Box, TextField } from "@mui/material";
import { useTheme } from "@mui/material/styles";

// Simple 7-day calendar with time selector
const SevenDayTimePicker = ({ value, onChange, disabled = false }) => {
  const theme = useTheme();
  const [selectedDate, setSelectedDate] = useState("");
  const [time, setTime] = useState("");

  // Sync internal state when value prop changes
  useEffect(() => {
    if (!value) {
      setSelectedDate("");
      setTime("");
      return;
    }
    const dt = new Date(value);
    if (!isNaN(dt)) {
      setSelectedDate(dt.toISOString().slice(0, 10));
      setTime(dt.toISOString().slice(11, 16));
    }
  }, [value]);

  useEffect(() => {
    if (selectedDate && time) {
      const iso = new Date(`${selectedDate}T${time}`).toISOString();
      onChange && onChange(iso);
    } else if (!selectedDate) {
      onChange && onChange("");
    }
  }, [selectedDate, time, onChange]);

  const days = Array.from({ length: 7 }).map((_, idx) => {
    const d = new Date();
    d.setDate(d.getDate() + idx);
    return d;
  });

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1,
        bgcolor: theme.palette.background.paper,
        p: 1,
        borderRadius: 1,
      }}
    >
      <Box sx={{ overflowX: "auto" }}>
        <table
          style={{
            width: "100%",
            tableLayout: "fixed",
            borderCollapse: "collapse",
          }}
        >
          <thead>
            <tr>
              {days.map((d) => (
                <th
                  key={d.toISOString() + "-label"}
                  style={{
                    padding: "4px",
                    textAlign: "center",
                    color: theme.palette.text.secondary,
                    border: `1px solid ${theme.palette.divider}`,
                    backgroundColor: theme.palette.background.default,
                  }}
                >
                  {d.toLocaleDateString(undefined, { weekday: "short" })}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              {days.map((d) => {
                const ds = d.toISOString().slice(0, 10);
                const isSel = selectedDate === ds;
                return (
                  <td
                    key={ds}
                    onClick={() => !disabled && setSelectedDate(ds)}
                    style={{
                      cursor: disabled ? "default" : "pointer",
                      textAlign: "center",
                      padding: "4px",
                      backgroundColor: isSel
                        ? theme.palette.primary.main
                        : theme.palette.background.paper,
                      color: isSel
                        ? theme.palette.getContrastText(
                            theme.palette.primary.main,
                          )
                        : theme.palette.text.primary,
                      border: `1px solid ${theme.palette.divider}`,
                    }}
                  >
                    {d.getDate()}
                  </td>
                );
              })}
            </tr>
          </tbody>
        </table>
      </Box>
      {selectedDate && (
        <TextField
          label="Time"
          type="time"
          value={time}
          onChange={(e) => setTime(e.target.value)}
          InputLabelProps={{ shrink: true }}
          inputProps={{ step: 300 }}
          disabled={disabled}
          size="small"
          sx={{ input: { bgcolor: theme.palette.background.paper } }}
        />
      )}
    </Box>
  );
};

export default SevenDayTimePicker;
