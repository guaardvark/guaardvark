// Which folder a Files-page action lands in.

import { translate } from '../../i18n';

/** Folder holding a stored path; '/' for a top-level item. */
export function parentPath(path) {
  if (!path) return '/';
  const trimmed = path.replace(/\/+$/, '');
  const cut = trimmed.lastIndexOf('/');
  if (cut <= 0) return '/';
  return trimmed.slice(0, cut);
}

/**
 * Folder a context-menu action (Paste, New Folder, Import) applies to.
 *
 * @param {'desktop'|'folder-window'|'folder'|'file'} contextType What was right-clicked.
 * @param {{path?: string}|null} item The right-clicked item; for 'folder-window'
 *   the window's folder with `path` set to the folder it is showing.
 * @returns {string} '/' for the desktop, otherwise a stored folder path.
 */
export function menuTargetPath(contextType, item) {
  if (!item) return '/';
  if (contextType === 'folder-window' || contextType === 'folder') return item.path || '/';
  if (contextType === 'file') return parentPath(item.path);
  return '/';
}

/** Short name for a folder path in messages. */
export function folderLabel(path) {
  if (!path || path === '/') return translate('documents.rootFolderName');
  const parts = path.split('/').filter(Boolean);
  return parts[parts.length - 1] || translate('documents.rootFolderName');
}
