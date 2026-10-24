// The media workspace is one page with five tabs. Each tab owns a route so it
// is linkable and reachable from the sidebar. Under software (workspace) chrome
// the Studio tool strip is this strip, so ImagesPage hides the duplicate row.

import { translate } from "../i18n";

// Labels are getters, read in the interface's language when the strip renders.
export const MEDIA_TABS = [
  { path: "/images", get label() { return translate("options.mediaTabs.mediaLibrary"); } },
  { path: "/batch-images", get label() { return translate("options.mediaTabs.imageGen"); } },
  { path: "/infographic", get label() { return translate("options.mediaTabs.infographic"); } },
  { path: "/video", get label() { return translate("options.mediaTabs.videoGen"); } },
  { path: "/upscaling", get label() { return translate("options.mediaTabs.upscaling"); } },
];

export const MEDIA_LIBRARY_TAB = 0;

/** Index of the tab owning `pathname`, defaulting to the Media Library. */
export function mediaTabIndexForPath(pathname) {
  const index = MEDIA_TABS.findIndex(
    (tab) => pathname === tab.path || pathname.startsWith(`${tab.path}/`),
  );
  return index === -1 ? MEDIA_LIBRARY_TAB : index;
}
