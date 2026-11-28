// What a person reads for an outreach draft row from /api/social-outreach.
//
// The API sends the words of a comment, post or reply as `text`. A Reddit
// share is stored differently: its draft_text is a JSON document
// {title, body, link_url}, which the poster reads back when it submits the
// link post, and `text` carries that document unchanged. These helpers read
// it so no view shows the JSON itself.

/** The parts of a share draft ({title, body, link, fields}), or null for any
 * other draft. `fields` is the stored document, kept so an edit writes back
 * every key it came with. */
export function shareParts(draft) {
  if ((draft?.action || "").toLowerCase() !== "share") return null;
  const raw = draft?.text ?? draft?.draft_text ?? "";
  if (typeof raw !== "string" || !raw.trimStart().startsWith("{")) return null;
  let fields;
  try {
    fields = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!fields || typeof fields !== "object" || Array.isArray(fields) || typeof fields.title !== "string") return null;
  return {
    title: fields.title.trim(),
    body: typeof fields.body === "string" ? fields.body.trim() : "",
    link: typeof fields.link_url === "string" ? fields.link_url.trim() : "",
    fields,
  };
}

/** A draft's words as plain text; a share reads as its title, then its body. */
export function draftWords(draft) {
  const share = shareParts(draft);
  if (share) return [share.title, share.body].filter(Boolean).join("\n\n");
  return draft?.text ?? draft?.draft_text ?? "";
}

/** The draft_text that stores an edited share, in the shape the poster reads. */
export function shareDraftText(share, { title, body }) {
  return JSON.stringify({ ...share.fields, title, body });
}
