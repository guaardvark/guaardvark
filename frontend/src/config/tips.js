/**
 * "Did you know" tips, shown one at a time by components/common/DidYouKnowTip.jsx.
 *
 * Each tip describes something the interface really does today (docs/interface.md
 * has the longer version). `route` adds a "Show me" button that opens that page;
 * `action` names one of the card's built-in actions instead ("shortcuts",
 * "floating-chat"). A tip whose route the active profile hides is skipped.
 * New tips go at the end, so a browser that has seen the list meets them first.
 */
import { translate } from "../i18n";

export const TIPS = Object.freeze([
  {
    id: "dashboard-card-menu",
    get text() {
      return translate("options.tips.dashboardCardMenu");
    },
    route: "/dashboard",
  },
  {
    id: "shortcuts",
    get text() {
      return translate("options.tips.shortcuts");
    },
    action: "shortcuts",
  },
  {
    id: "mic-hands-free",
    get text() {
      return translate("options.tips.micHandsFree");
    },
  },
  {
    id: "floating-chat",
    get text() {
      return translate("options.tips.floatingChat");
    },
    action: "floating-chat",
  },
  {
    id: "drop-into-files",
    get text() {
      return translate("options.tips.dropIntoFiles");
    },
    route: "/documents",
  },
  {
    id: "dashboard-hidden-cards",
    get text() {
      return translate("options.tips.dashboardHiddenCards");
    },
    route: "/dashboard",
  },
  {
    id: "move-resize-cards",
    get text() {
      return translate("options.tips.moveResizeCards");
    },
    route: "/dashboard",
  },
  {
    id: "settings-hover-help",
    get text() {
      return translate("options.tips.settingsHoverHelp");
    },
    route: "/settings",
  },
  {
    id: "navigation-modes",
    get text() {
      return translate("options.tips.navigationModes");
    },
    route: "/settings#settings-general",
  },
  {
    id: "drop-into-chat",
    get text() {
      return translate("options.tips.dropIntoChat");
    },
    route: "/chat",
  },
  {
    id: "mic-push-to-talk",
    get text() {
      return translate("options.tips.micPushToTalk");
    },
  },
  {
    id: "dashboard-layouts",
    get text() {
      return translate("options.tips.dashboardLayouts");
    },
    route: "/dashboard",
  },
  {
    id: "footer-jobs",
    get text() {
      return translate("options.tips.footerJobs");
    },
  },
  {
    id: "update-notice",
    get text() {
      return translate("options.tips.updateNotice");
    },
  },
  {
    id: "right-click-everywhere",
    get text() {
      return translate("options.tips.rightClickEverywhere");
    },
  },
]);
