// What "approve" and "reject" mean for a change the inbound guard holds, in one
// place, so the Approvals page and Settings act the same. The backend lands the
// change before it records the approval; a change that can no longer land as it
// was held stays open and the reason comes back as the error.
import { inboundGuardService } from "./inboundGuardService";

/** Held items waiting on a person: enforced holds and blocks, not reports or git records. */
export const isActionable = (scan) =>
  scan?.status === "open" && scan.mode === "enforce" && scan.source !== "watch-audit";

export const approveHeldChange = (entry, { note = "", overrideBlock = false } = {}) =>
  entry.kind === "git"
    ? inboundGuardService.approveGit(entry.item.digest, note, { overrideBlock })
    : inboundGuardService.approveScan(entry.item.id, { note, overrideBlock });

export const rejectHeldChange = (entry, { note = "" } = {}) => {
  if (entry.kind === "git") {
    return Promise.reject(new Error("A git record can only be approved; undo the merge in git to reject it."));
  }
  return inboundGuardService.rejectScan(entry.item.id, { note });
};
