// Captions under a note sent to the screen agent while it was working.
// Keyed by the message's `agentNote` status (ChatPage, FloatingChatCard).
// Worded when shown, so a caption follows the interface language.
import { translate } from "../../i18n";

export const agentNoteCaptions = () => ({
  sending: translate("chat.agentNote.sending"),
  sent: translate("chat.agentNote.sent"),
  stopping: translate("chat.agentNote.stopping"),
  late: translate("chat.agentNote.late"),
  failed: translate("chat.agentNote.failed"),
});

export const agentNoteCaption = (status) => {
  const captions = agentNoteCaptions();
  return captions[status] || captions.sent;
};
