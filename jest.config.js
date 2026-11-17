/** @type {import('jest').Config} */
module.exports = {
  testEnvironment: 'jsdom',
  roots: ['<rootDir>/tests'],
  testMatch: [
    '**/unit/**/*.test.js',
    '**/integration/**/*.test.js'
  ],
  moduleNameMapper: {
    '^@lib/(.*)$': '<rootDir>/extension/lib/$1',
    '^@extension/(.*)$': '<rootDir>/extension/$1'
  },
  setupFiles: ['<rootDir>/tests/crypto-setup.js'],
  setupFilesAfterEnv: ['<rootDir>/tests/setup.js'],
  collectCoverageFrom: [
    'extension/lib/**/*.js',
    'extension/popup/**/*.js',
    'extension/options/**/*.js',
    'extension/content/**/*.js',
    'extension/background/**/*.js',
    '!**/*.min.js'
  ],
  coverageDirectory: 'coverage',
  coverageReporters: ['text', 'lcov', 'html'],
  coverageThreshold: {
    global: {
      branches: 30,
      functions: 37,
      lines: 35,
      statements: 34
    },
    './extension/lib/': {
      branches: 70,
      functions: 80,
      lines: 80,
      statements: 78
    },
    './extension/background/service-worker.js': {
      branches: 50,
      functions: 55,
      lines: 60,
      statements: 60
    },
    './extension/content/content-helpers.js': {
      branches: 65,
      functions: 95,
      lines: 90,
      statements: 85
    },
    './extension/content/content.js': {
      branches: 15,
      functions: 20,
      lines: 23,
      statements: 21
    },
    './extension/options/options.js': {
      branches: 15,
      functions: 15,
      lines: 15,
      statements: 15
    },
    './extension/popup/popup.js': {
      branches: 40,
      functions: 40,
      lines: 50,
      statements: 50
    }
  },
  testTimeout: 10000,
  verbose: true
};
