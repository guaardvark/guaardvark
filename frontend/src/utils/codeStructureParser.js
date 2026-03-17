/**
 * Code Structure Parser
 *
 * Fast regex-based extraction of code structure (functions, classes, imports, exports).
 * No LLM calls - pure parsing for instant results.
 * Used by Smart Context Builder to identify relevant code sections.
 */

/**
 * Parse code structure and extract functions, classes, imports, exports with line numbers
 * @param {string} code - The source code to parse
 * @param {string} language - Programming language (javascript, typescript, python, etc.)
 * @returns {object} Parsed structure with functions, classes, imports, exports
 */
export const parseCodeStructure = (code, language = 'javascript') => {
  if (!code || typeof code !== 'string') {
    return { functions: [], classes: [], imports: [], exports: [], variables: [] };
  }

  const lines = code.split('\n');
  const lang = language.toLowerCase();

  if (lang === 'python') {
    return parsePythonStructure(lines);
  } else {
    // JavaScript, TypeScript, JSX, TSX
    return parseJavaScriptStructure(lines, lang);
  }
};

/**
 * Parse JavaScript/TypeScript/JSX/TSX code structure
 */
// eslint-disable-next-line no-unused-vars
const parseJavaScriptStructure = (lines, language) => {
  const structure = {
    functions: [],
    classes: [],
    imports: [],
    exports: [],
    variables: [],
    hooks: [], // React hooks
    components: [] // React components
  };

  // Patterns for JavaScript/TypeScript
  const patterns = {
    // import statements
    import: /^import\s+(?:(\{[^}]+\})|(\*\s+as\s+\w+)|(\w+))(?:\s*,\s*(?:(\{[^}]+\})|(\w+)))?\s+from\s+['"]([^'"]+)['"]/,
    importType: /^import\s+type\s+/,
    require: /(?:const|let|var)\s+(?:(\{[^}]+\})|(\w+))\s*=\s*require\s*\(['"]([^'"]+)['"]\)/,

    // function declarations
    functionDecl: /^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(/,
    arrowFunction: /^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>/,
    arrowFunctionNoParams: /^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\w+\s*=>/,
    methodShorthand: /^\s+(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{/,

    // class declarations
    classDecl: /^(?:export\s+)?(?:default\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?/,

    // exports
    exportDefault: /^export\s+default\s+(?:function\s+)?(\w+)?/,
    exportNamed: /^export\s+(?:const|let|var|function|class)\s+(\w+)/,
    exportFrom: /^export\s+\{([^}]+)\}\s+from/,

    // React specific
    useState: /(?:const|let)\s+\[(\w+),\s*set(\w+)\]\s*=\s*useState/,
    useEffect: /useEffect\s*\(/,
    useCallback: /(?:const|let)\s+(\w+)\s*=\s*useCallback/,
    useMemo: /(?:const|let)\s+(\w+)\s*=\s*useMemo/,
    useRef: /(?:const|let)\s+(\w+)\s*=\s*useRef/,

    // React component (PascalCase function returning JSX)
    component: /^(?:export\s+)?(?:const|function)\s+([A-Z]\w+)\s*(?:=|[(\s])/,

    // Important variables/constants
    constDecl: /^(?:export\s+)?const\s+(\w+)\s*=/,
  };

  let currentClass = null;
  let braceDepth = 0;
  let classStartDepth = 0;

  lines.forEach((line, index) => {
    const lineNum = index + 1;
    const trimmed = line.trim();

    // Skip empty lines and comments
    if (!trimmed || trimmed.startsWith('//') || trimmed.startsWith('/*') || trimmed.startsWith('*')) {
      return;
    }

    // Track brace depth for class scope
    braceDepth += (line.match(/\{/g) || []).length;
    braceDepth -= (line.match(/\}/g) || []).length;

    // End of class
    if (currentClass && braceDepth < classStartDepth) {
      currentClass = null;
    }

    // Imports
    let match = trimmed.match(patterns.import);
    if (match) {
      structure.imports.push({
        line: lineNum,
        source: match[6],
        named: match[1] || match[4] || null,
        default: match[3] || match[5] || null,
        namespace: match[2] || null,
        isType: patterns.importType.test(trimmed)
      });
      return;
    }

    match = trimmed.match(patterns.require);
    if (match) {
      structure.imports.push({
        line: lineNum,
        source: match[3],
        named: match[1] || null,
        default: match[2] || null,
        isRequire: true
      });
      return;
    }

    // Classes
    match = trimmed.match(patterns.classDecl);
    if (match) {
      currentClass = {
        name: match[1],
        extends: match[2] || null,
        line: lineNum,
        methods: []
      };
      classStartDepth = braceDepth;
      structure.classes.push(currentClass);
      return;
    }

    // Class methods
    if (currentClass) {
      match = trimmed.match(patterns.methodShorthand);
      if (match && !['if', 'for', 'while', 'switch', 'catch'].includes(match[1])) {
        currentClass.methods.push({
          name: match[1],
          line: lineNum,
          async: trimmed.includes('async')
        });
        return;
      }
    }

    // Function declarations
    match = trimmed.match(patterns.functionDecl);
    if (match) {
      structure.functions.push({
        name: match[1],
        line: lineNum,
        async: trimmed.includes('async'),
        exported: trimmed.startsWith('export')
      });
      return;
    }

    // Arrow functions
    match = trimmed.match(patterns.arrowFunction) || trimmed.match(patterns.arrowFunctionNoParams);
    if (match) {
      const name = match[1];
      // Check if it's a React component (PascalCase)
      if (/^[A-Z]/.test(name)) {
        structure.components.push({
          name,
          line: lineNum,
          exported: trimmed.startsWith('export')
        });
      } else {
        structure.functions.push({
          name,
          line: lineNum,
          async: trimmed.includes('async'),
          arrow: true,
          exported: trimmed.startsWith('export')
        });
      }
      return;
    }

    // React hooks
    match = trimmed.match(patterns.useState);
    if (match) {
      structure.hooks.push({
        type: 'useState',
        name: match[1],
        setter: `set${match[2]}`,
        line: lineNum
      });
      return;
    }

    match = trimmed.match(patterns.useCallback);
    if (match) {
      structure.hooks.push({
        type: 'useCallback',
        name: match[1],
        line: lineNum
      });
      structure.functions.push({
        name: match[1],
        line: lineNum,
        isCallback: true
      });
      return;
    }

    match = trimmed.match(patterns.useMemo);
    if (match) {
      structure.hooks.push({
        type: 'useMemo',
        name: match[1],
        line: lineNum
      });
      return;
    }

    // Exports
    match = trimmed.match(patterns.exportDefault);
    if (match) {
      structure.exports.push({
        name: match[1] || 'default',
        line: lineNum,
        isDefault: true
      });
      return;
    }

    match = trimmed.match(patterns.exportNamed);
    if (match) {
      structure.exports.push({
        name: match[1],
        line: lineNum,
        isDefault: false
      });
      return;
    }

    // Important constants (API endpoints, config, etc.)
    match = trimmed.match(patterns.constDecl);
    if (match) {
      const name = match[1];
      // Only track UPPER_CASE constants or important-looking ones
      if (/^[A-Z_]+$/.test(name) || name.includes('URL') || name.includes('API') || name.includes('CONFIG')) {
        structure.variables.push({
          name,
          line: lineNum,
          exported: trimmed.startsWith('export')
        });
      }
    }
  });

  return structure;
};

/**
 * Parse Python code structure
 */
const parsePythonStructure = (lines) => {
  const structure = {
    functions: [],
    classes: [],
    imports: [],
    exports: [], // Python doesn't have exports in the same way
    variables: [],
    decorators: []
  };

  const patterns = {
    import: /^import\s+(\w+(?:\.\w+)*)/,
    fromImport: /^from\s+(\w+(?:\.\w+)*)\s+import\s+(.+)/,
    function: /^(?:async\s+)?def\s+(\w+)\s*\(/,
    classDecl: /^class\s+(\w+)(?:\s*\(([^)]*)\))?:/,
    decorator: /^@(\w+(?:\.\w+)*)/,
    variable: /^([A-Z_][A-Z0-9_]*)\s*=/,  // UPPER_CASE constants
  };

  let currentClass = null;
  let currentIndent = 0;
  let pendingDecorators = [];

  lines.forEach((line, index) => {
    const lineNum = index + 1;
    const trimmed = line.trim();
    const indent = line.search(/\S/);

    if (!trimmed || trimmed.startsWith('#')) {
      return;
    }

    // Track class scope by indentation
    if (currentClass && indent <= currentIndent && !trimmed.startsWith('@')) {
      currentClass = null;
    }

    // Imports
    let match = trimmed.match(patterns.import);
    if (match) {
      structure.imports.push({
        line: lineNum,
        module: match[1],
        type: 'import'
      });
      return;
    }

    match = trimmed.match(patterns.fromImport);
    if (match) {
      structure.imports.push({
        line: lineNum,
        module: match[1],
        names: match[2].split(',').map(s => s.trim()),
        type: 'from'
      });
      return;
    }

    // Decorators
    match = trimmed.match(patterns.decorator);
    if (match) {
      pendingDecorators.push({
        name: match[1],
        line: lineNum
      });
      return;
    }

    // Classes
    match = trimmed.match(patterns.classDecl);
    if (match) {
      currentClass = {
        name: match[1],
        bases: match[2] ? match[2].split(',').map(s => s.trim()) : [],
        line: lineNum,
        methods: [],
        decorators: [...pendingDecorators]
      };
      currentIndent = indent;
      structure.classes.push(currentClass);
      pendingDecorators = [];
      return;
    }

    // Functions/Methods
    match = trimmed.match(patterns.function);
    if (match) {
      const fn = {
        name: match[1],
        line: lineNum,
        async: trimmed.startsWith('async'),
        decorators: [...pendingDecorators]
      };

      if (currentClass && indent > currentIndent) {
        currentClass.methods.push(fn);
      } else {
        structure.functions.push(fn);
        currentClass = null;
      }
      pendingDecorators = [];
      return;
    }

    // Constants
    match = trimmed.match(patterns.variable);
    if (match && indent === 0) {
      structure.variables.push({
        name: match[1],
        line: lineNum
      });
    }

    // Clear decorators if we hit something else
    if (pendingDecorators.length > 0 && !trimmed.startsWith('@')) {
      pendingDecorators = [];
    }
  });

  return structure;
};

/**
 * Extract a code section from start line to end of function/class
 * @param {string} code - Full source code
 * @param {number} startLine - 1-indexed start line
 * @param {string} language - Programming language
 * @returns {object} Extracted code with metadata
 */
export const extractCodeSection = (code, startLine, language = 'javascript') => {
  const lines = code.split('\n');
  const startIndex = startLine - 1;

  if (startIndex < 0 || startIndex >= lines.length) {
    return { code: '', startLine, endLine: startLine, lineCount: 0 };
  }

  const lang = language.toLowerCase();
  let endIndex = startIndex;

  if (lang === 'python') {
    // Python: find end by indentation
    const startIndent = lines[startIndex].search(/\S/);
    for (let i = startIndex + 1; i < lines.length; i++) {
      const line = lines[i];
      const trimmed = line.trim();
      if (!trimmed) continue; // Skip empty lines

      const indent = line.search(/\S/);
      if (indent <= startIndent && trimmed) {
        endIndex = i - 1;
        break;
      }
      endIndex = i;
    }
  } else {
    // JavaScript: find end by brace matching
    let braceCount = 0;
    let started = false;

    for (let i = startIndex; i < lines.length; i++) {
      const line = lines[i];
      braceCount += (line.match(/\{/g) || []).length;
      braceCount -= (line.match(/\}/g) || []).length;

      if (braceCount > 0) started = true;
      if (started && braceCount === 0) {
        endIndex = i;
        break;
      }
      endIndex = i;
    }
  }

  const extractedLines = lines.slice(startIndex, endIndex + 1);

  return {
    code: extractedLines.join('\n'),
    startLine,
    endLine: endIndex + 1,
    lineCount: extractedLines.length
  };
};

/**
 * Find code elements that match a search query
 * @param {object} structure - Parsed code structure
 * @param {string} query - User's search query
 * @returns {array} Matching elements with relevance scores
 */
export const findRelevantElements = (structure, query) => {
  if (!query || !structure) return [];

  const queryLower = query.toLowerCase();
  const queryWords = queryLower.split(/\s+/).filter(w => w.length > 2);
  const matches = [];

  // eslint-disable-next-line no-unused-vars
  const scoreMatch = (name, type) => {
    const nameLower = name.toLowerCase();
    let score = 0;

    // Exact match
    if (queryLower.includes(nameLower) || nameLower.includes(queryLower)) {
      score += 10;
    }

    // Word matches
    queryWords.forEach(word => {
      if (nameLower.includes(word)) score += 5;
    });

    // Camel case word matches
    const camelWords = name.split(/(?=[A-Z])/).map(w => w.toLowerCase());
    camelWords.forEach(word => {
      if (queryWords.includes(word)) score += 3;
    });

    return score;
  };

  // Score functions
  structure.functions?.forEach(fn => {
    const score = scoreMatch(fn.name, 'function');
    if (score > 0) {
      matches.push({ ...fn, type: 'function', score });
    }
  });

  // Score classes and their methods
  structure.classes?.forEach(cls => {
    const classScore = scoreMatch(cls.name, 'class');
    if (classScore > 0) {
      matches.push({ ...cls, type: 'class', score: classScore });
    }

    cls.methods?.forEach(method => {
      const methodScore = scoreMatch(method.name, 'method');
      if (methodScore > 0) {
        matches.push({
          ...method,
          type: 'method',
          className: cls.name,
          score: methodScore
        });
      }
    });
  });

  // Score components
  structure.components?.forEach(comp => {
    const score = scoreMatch(comp.name, 'component');
    if (score > 0) {
      matches.push({ ...comp, type: 'component', score });
    }
  });

  // Score hooks
  structure.hooks?.forEach(hook => {
    const score = scoreMatch(hook.name, 'hook');
    if (score > 0) {
      matches.push({ ...hook, type: 'hook', score });
    }
  });

  // Sort by score descending
  return matches.sort((a, b) => b.score - a.score);
};

/**
 * Generate a concise summary of code structure
 * @param {object} structure - Parsed code structure
 * @returns {string} Human-readable summary
 */
export const generateStructureSummary = (structure) => {
  const parts = [];

  if (structure.imports?.length > 0) {
    const sources = [...new Set(structure.imports.map(i => i.source || i.module))];
    parts.push(`Imports: ${sources.slice(0, 5).join(', ')}${sources.length > 5 ? ` (+${sources.length - 5} more)` : ''}`);
  }

  if (structure.components?.length > 0) {
    parts.push(`Components: ${structure.components.map(c => c.name).join(', ')}`);
  }

  if (structure.classes?.length > 0) {
    structure.classes.forEach(cls => {
      const methods = cls.methods?.map(m => m.name).join(', ') || 'none';
      parts.push(`Class ${cls.name}: methods [${methods}]`);
    });
  }

  if (structure.functions?.length > 0) {
    const fns = structure.functions.map(f => f.name);
    parts.push(`Functions: ${fns.slice(0, 8).join(', ')}${fns.length > 8 ? ` (+${fns.length - 8} more)` : ''}`);
  }

  if (structure.hooks?.length > 0) {
    const hooks = structure.hooks.map(h => h.name);
    parts.push(`Hooks: ${hooks.join(', ')}`);
  }

  if (structure.exports?.length > 0) {
    const exports = structure.exports.map(e => e.isDefault ? `default(${e.name})` : e.name);
    parts.push(`Exports: ${exports.join(', ')}`);
  }

  return parts.join('\n');
};

export default {
  parseCodeStructure,
  extractCodeSection,
  findRelevantElements,
  generateStructureSummary
};
