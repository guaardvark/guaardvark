// The media workspace is one page with five tabs. Each tab owns a route so it
// is linkable and reachable from the sidebar, and so the tab strip stays on
// screen no matter which of them the user landed on.

export const MEDIA_TABS = [
  { path: "/images", label: "Media Library" },
  { path: "/batch-images", label: "Image Gen" },
  { path: "/infographic", label: "Infographic" },
  { path: "/video", label: "Video Gen" },
  { path: "/upscaling", label: "Upscaling" },
];

export const MEDIA_LIBRARY_TAB = 0;

/** Index of the tab owning `pathname`, defaulting to the Media Library. */
export function mediaTabIndexForPath(pathname) {
  const index = MEDIA_TABS.findIndex(
    (tab) => pathname === tab.path || pathname.startsWith(`${tab.path}/`),
  );
  return index === -1 ? MEDIA_LIBRARY_TAB : index;
}
