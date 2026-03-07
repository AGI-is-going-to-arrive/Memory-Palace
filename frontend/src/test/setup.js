import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

if (typeof window !== 'undefined') {
  Object.defineProperty(window, 'scrollTo', {
    value: () => {},
    writable: true,
    configurable: true,
  });

  const existingStorage = window.localStorage;
  if (
    !existingStorage ||
    typeof existingStorage.getItem !== 'function' ||
    typeof existingStorage.setItem !== 'function' ||
    typeof existingStorage.removeItem !== 'function' ||
    typeof existingStorage.clear !== 'function'
  ) {
    const storage = new Map();
    Object.defineProperty(window, 'localStorage', {
      value: {
        getItem: (key) => (storage.has(key) ? storage.get(key) : null),
        setItem: (key, value) => {
          storage.set(String(key), String(value));
        },
        removeItem: (key) => {
          storage.delete(String(key));
        },
        clear: () => {
          storage.clear();
        },
      },
      writable: true,
      configurable: true,
    });
  }
}

afterEach(() => {
  cleanup();
});
