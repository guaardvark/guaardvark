// frontend/src/test/setup.js
// Test setup file for vitest
/* global global */

import { expect, afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import * as matchers from '@testing-library/jest-dom/matchers';

// Extend Vitest's expect with testing-library matchers
expect.extend(matchers);

// Cleanup after each test
afterEach(() => {
  cleanup();
});

// Mock fetch globally
global.fetch = vi.fn();

// Mock console methods to prevent noise in tests
global.console = {
  ...console,
  log: vi.fn(),
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
};

// Mock AbortController
global.AbortController = class {
  constructor() {
    this.signal = {
      aborted: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
  }
  abort() {
    this.signal.aborted = true;
  }
};

// Mock DOMPurify
vi.mock('dompurify', () => ({
  default: {
    sanitize: (input) => input,
  },
}));
