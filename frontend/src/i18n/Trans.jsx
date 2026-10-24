// react-i18next's Trans with the values escaped before the sentence is parsed
// for tags, then unescaped for display. Without it a value holding "<" or "&"
// (a file name, a search, a server error) would be read as markup.
import React from "react";
import { Trans as BaseTrans } from "react-i18next";

const ESCAPED = { interpolation: { escapeValue: true } };

export default function Trans({ tOptions, ...props }) {
  return <BaseTrans tOptions={{ ...ESCAPED, ...tOptions }} shouldUnescape {...props} />;
}
