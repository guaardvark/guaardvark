/**
 * Language Detector Utility for Monaco Editor
 *
 * Provides extension-based and content-based language detection for code files.
 * Mimics Cursor IDE behavior where tabs show filename.ext with proper syntax highlighting.
 */

// Extension to Monaco language mapping
const EXTENSION_MAP = {
  // Web Technologies
  'html': 'html',
  'htm': 'html',
  'xhtml': 'html',
  'css': 'css',
  'scss': 'scss',
  'sass': 'scss',
  'less': 'less',
  'styl': 'stylus',

  // JavaScript/TypeScript
  'js': 'javascript',
  'mjs': 'javascript',
  'cjs': 'javascript',
  'jsx': 'javascript',
  'ts': 'typescript',
  'tsx': 'typescript',
  'vue': 'html',
  'svelte': 'html',

  // Data Formats
  'json': 'json',
  'jsonc': 'json',
  'json5': 'json',
  'xml': 'xml',
  'svg': 'xml',
  'yaml': 'yaml',
  'yml': 'yaml',
  'toml': 'ini',
  'ini': 'ini',
  'conf': 'ini',
  'cfg': 'ini',

  // Programming Languages
  'py': 'python',
  'pyw': 'python',
  'pyi': 'python',
  'java': 'java',
  'kt': 'kotlin',
  'kts': 'kotlin',
  'scala': 'scala',
  'groovy': 'groovy',
  'c': 'c',
  'h': 'c',
  'cpp': 'cpp',
  'cxx': 'cpp',
  'cc': 'cpp',
  'hpp': 'cpp',
  'hxx': 'cpp',
  'cs': 'csharp',
  'fs': 'fsharp',
  'go': 'go',
  'rs': 'rust',
  'rb': 'ruby',
  'php': 'php',
  'swift': 'swift',
  'r': 'r',
  'lua': 'lua',
  'pl': 'perl',
  'pm': 'perl',
  'ex': 'elixir',
  'exs': 'elixir',
  'erl': 'erlang',
  'hrl': 'erlang',
  'clj': 'clojure',
  'cljs': 'clojure',
  'dart': 'dart',
  'zig': 'zig',
  'nim': 'nim',
  'v': 'v',

  // Shell/Config
  'sh': 'shell',
  'bash': 'shell',
  'zsh': 'shell',
  'fish': 'shell',
  'ps1': 'powershell',
  'psm1': 'powershell',
  'bat': 'bat',
  'cmd': 'bat',
  'dockerfile': 'dockerfile',
  'makefile': 'makefile',
  'mk': 'makefile',

  // Documentation
  'md': 'markdown',
  'markdown': 'markdown',
  'mdx': 'markdown',
  'rst': 'restructuredtext',
  'txt': 'plaintext',
  'log': 'plaintext',
  'text': 'plaintext',

  // SQL
  'sql': 'sql',
  'mysql': 'sql',
  'pgsql': 'sql',
  'sqlite': 'sql',

  // Other
  'graphql': 'graphql',
  'gql': 'graphql',
  'proto': 'protobuf',
  'tf': 'hcl',
  'tfvars': 'hcl',
  'hcl': 'hcl',
};

// Special filenames that map to specific languages
const FILENAME_MAP = {
  'dockerfile': 'dockerfile',
  'makefile': 'makefile',
  'gnumakefile': 'makefile',
  'cmakelists.txt': 'cmake',
  'gemfile': 'ruby',
  'rakefile': 'ruby',
  'podfile': 'ruby',
  'vagrantfile': 'ruby',
  '.gitignore': 'ignore',
  '.dockerignore': 'ignore',
  '.npmignore': 'ignore',
  '.eslintrc': 'json',
  '.prettierrc': 'json',
  '.babelrc': 'json',
  'tsconfig.json': 'json',
  'package.json': 'json',
  'composer.json': 'json',
  'cargo.toml': 'toml',
  'pyproject.toml': 'toml',
  '.env': 'dotenv',
  '.env.local': 'dotenv',
  '.env.development': 'dotenv',
  '.env.production': 'dotenv',
};

/**
 * Get Monaco language from filename/extension
 * @param {string} filename - The filename (e.g., "index.html", "app.py")
 * @returns {string} Monaco language identifier
 */
export const getLanguageFromFilename = (filename) => {
  if (!filename) return 'plaintext';

  const lowerFilename = filename.toLowerCase();

  // Check special filenames first
  if (FILENAME_MAP[lowerFilename]) {
    return FILENAME_MAP[lowerFilename];
  }

  // Get extension
  const lastDot = filename.lastIndexOf('.');
  if (lastDot === -1 || lastDot === filename.length - 1) {
    // No extension - check if it's a known filename without extension
    return FILENAME_MAP[lowerFilename] || 'plaintext';
  }

  const ext = filename.substring(lastDot + 1).toLowerCase();
  return EXTENSION_MAP[ext] || 'plaintext';
};

/**
 * Content-based language detection for unsaved/pasted content
 * Used as fallback when filename doesn't provide language info
 * @param {string} content - The code content to analyze
 * @returns {string} Monaco language identifier
 */
export const detectLanguageFromContent = (content) => {
  if (!content || typeof content !== 'string') return 'plaintext';

  const trimmed = content.trim();
  const firstLine = trimmed.split('\n')[0].trim();

  // HTML detection
  if (trimmed.startsWith('<!DOCTYPE') ||
      trimmed.startsWith('<!doctype') ||
      trimmed.startsWith('<html') ||
      trimmed.startsWith('<HTML') ||
      (trimmed.startsWith('<') && trimmed.includes('</') && /<\w+[^>]*>/.test(trimmed))) {
    return 'html';
  }

  // XML detection
  if (trimmed.startsWith('<?xml')) {
    return 'xml';
  }

  // JSON detection
  if ((trimmed.startsWith('{') && trimmed.endsWith('}')) ||
      (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
    try {
      JSON.parse(trimmed);
      return 'json';
    } catch {
      // Not valid JSON, continue detection
    }
  }

  // Shebang detection
  if (firstLine.startsWith('#!')) {
    if (firstLine.includes('python')) return 'python';
    if (firstLine.includes('node') || firstLine.includes('deno')) return 'javascript';
    if (firstLine.includes('ruby')) return 'ruby';
    if (firstLine.includes('perl')) return 'perl';
    if (firstLine.includes('php')) return 'php';
    if (firstLine.includes('bash') || firstLine.includes('/sh')) return 'shell';
    return 'shell'; // Default for other shebangs
  }

  // Python detection
  if (/^(def |class |import |from .+ import |if __name__|@\w+)/.test(trimmed) ||
      /:\s*$/.test(firstLine) && /^\s{4}/.test(trimmed.split('\n')[1] || '')) {
    return 'python';
  }

  // JavaScript/TypeScript detection
  if (/^(const |let |var |function |import |export |class |async |=>\s*{)/.test(trimmed) ||
      /^(interface |type |enum |namespace )/.test(trimmed)) {
    // Check for TypeScript-specific syntax
    if (/^(interface |type |enum |namespace )/.test(trimmed) ||
        /:\s*(string|number|boolean|any|void|never)\b/.test(trimmed)) {
      return 'typescript';
    }
    return 'javascript';
  }

  // CSS detection
  if (/^(@import|@media|@keyframes|\*|body|html|div|\.[\w-]+|#[\w-]+)\s*\{/.test(trimmed) ||
      /:\s*(#[0-9a-f]+|rgba?\(|hsla?\(|\d+px|\d+em|\d+rem|inherit|none|auto)/i.test(trimmed)) {
    return 'css';
  }

  // SQL detection
  if (/^(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|WITH)\s/i.test(trimmed)) {
    return 'sql';
  }

  // YAML detection
  if (/^[\w-]+:\s*[^{]/.test(firstLine) && !trimmed.includes('{')) {
    return 'yaml';
  }

  // Markdown detection
  if (/^#{1,6}\s/.test(firstLine) || /^\*\*/.test(trimmed) || /^\[.+\]\(.+\)/.test(trimmed)) {
    return 'markdown';
  }

  return 'plaintext';
};

/**
 * Get display name for a language
 * @param {string} languageId - Monaco language identifier
 * @returns {string} Human-readable language name
 */
export const getLanguageDisplayName = (languageId) => {
  const displayNames = {
    'javascript': 'JavaScript',
    'typescript': 'TypeScript',
    'html': 'HTML',
    'css': 'CSS',
    'scss': 'SCSS',
    'less': 'Less',
    'json': 'JSON',
    'xml': 'XML',
    'yaml': 'YAML',
    'python': 'Python',
    'java': 'Java',
    'kotlin': 'Kotlin',
    'cpp': 'C++',
    'c': 'C',
    'csharp': 'C#',
    'go': 'Go',
    'rust': 'Rust',
    'ruby': 'Ruby',
    'php': 'PHP',
    'swift': 'Swift',
    'shell': 'Shell',
    'powershell': 'PowerShell',
    'sql': 'SQL',
    'markdown': 'Markdown',
    'dockerfile': 'Dockerfile',
    'plaintext': 'Plain Text',
  };

  return displayNames[languageId] || languageId;
};

/**
 * Get file icon based on language (for UI display)
 * @param {string} languageId - Monaco language identifier
 * @returns {string} Icon name or emoji
 */
export const getLanguageIcon = (languageId) => {
  const icons = {
    'javascript': '📜',
    'typescript': '📘',
    'html': '🌐',
    'css': '🎨',
    'python': '🐍',
    'java': '☕',
    'go': '🐹',
    'rust': '🦀',
    'ruby': '💎',
    'shell': '🖥️',
    'sql': '🗄️',
    'markdown': '📝',
    'json': '📋',
    'yaml': '📋',
    'dockerfile': '🐳',
  };

  return icons[languageId] || '📄';
};

export default {
  getLanguageFromFilename,
  detectLanguageFromContent,
  getLanguageDisplayName,
  getLanguageIcon,
  EXTENSION_MAP,
  FILENAME_MAP,
};
