/** @type {import("eslint").Linter.Config} */
module.exports = {
  root: true,
  env: {
    browser: true,
    node: true,
    es2022: true,
    jest: true
  },
  parserOptions: {
    ecmaVersion: "latest",
    sourceType: "module"
  },
  extends: ["eslint:recommended"],
  ignorePatterns: [
    "backend/venv/**",
    "backend/models_cache/**",
    "playwright-report/**",
    "test-results/**",
    "coverage/**",
    "dist/**",
    "node_modules/**"
  ],
  globals: {
    chrome: "readonly",
    importScripts: "readonly",
    TranslationProvider: "readonly",
    LocalBackendProvider: "readonly",
    MangaTranslatorSettings: "readonly",
    MangaTranslatorTranslatedImage: "readonly"
  },
  overrides: [
    {
      files: ["extension/background/**/*.js"],
      env: { serviceworker: true }
    }
  ],
  rules: {
    "no-unused-vars": ["error", { "argsIgnorePattern": "^_" }],
    "no-constant-condition": ["error", { checkLoops: false }]
  }
};

