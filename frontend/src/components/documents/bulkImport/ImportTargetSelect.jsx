// "Into": the Files folder an import lands in. The top-level folders, the
// folder the page was opened for (any depth), and New folder…, which names a
// top-level folder the import creates.
import React from "react";
import { Divider, ListSubheader, MenuItem, TextField } from "@mui/material";
import { translate, useUiTranslation } from "../../../i18n";

export const DEFAULT_INTO = { id: null, path: null, name: "Imports", isNew: true };

const keyOf = (into) => (into?.id != null ? `id:${into.id}` : `new:${into?.name || ""}`);
const NEW_FOLDER = "__new_folder__";

/** A folder's place in Files as a person reads it: "Files › Clients › Acme". */
export const intoLabel = (into) => {
  if (!into) return "";
  const parts = (into.path || into.name || "").split("/").filter(Boolean);
  return [translate("upload.bulk.filesPage"), ...parts].join(" › ");
};

const ImportTargetSelect = ({ folders, value, onChange, onNewFolder, disabled = false }) => {
  const { t } = useUiTranslation();
  const options = folders.map((f) => ({ id: f.id, path: f.path, name: f.name, isNew: false }));
  const hasDefault = options.some((o) => o.name === DEFAULT_INTO.name);
  if (!hasDefault) options.unshift(DEFAULT_INTO);
  if (value && !options.some((o) => keyOf(o) === keyOf(value))) options.unshift(value);

  const handleChange = (event) => {
    const key = event.target.value;
    if (key === NEW_FOLDER) {
      onNewFolder?.();
      return;
    }
    const picked = options.find((o) => keyOf(o) === key);
    if (picked) onChange(picked);
  };

  return (
    <TextField
      select
      size="small"
      label={t("upload.bulk.into.label")}
      value={keyOf(value)}
      onChange={handleChange}
      disabled={disabled}
      sx={{ minWidth: 220 }}
      SelectProps={{
        renderValue: () => {
          const name = (value?.path || value?.name || "").split("/").filter(Boolean).join(" › ");
          return value?.isNew ? t("upload.bulk.into.newFolder", { name }) : name;
        },
      }}
      inputProps={{ "data-testid": "import-into" }}
    >
      <ListSubheader>{t("upload.bulk.into.foldersInFiles")}</ListSubheader>
      {options.map((o) => {
        const name = (o.path || o.name).split("/").filter(Boolean).join(" › ");
        return (
          <MenuItem key={keyOf(o)} value={keyOf(o)}>
            {o.isNew ? t("upload.bulk.into.newFolder", { name }) : name}
          </MenuItem>
        );
      })}
      <Divider />
      <MenuItem value={NEW_FOLDER}>{t("upload.bulk.into.newFolderOption")}</MenuItem>
    </TextField>
  );
};

export default ImportTargetSelect;
