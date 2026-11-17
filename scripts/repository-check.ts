/**
 * Repository boundary checks: forbidden/generated paths stay out of Git, the
 * required WXT + src-layout files exist, legacy pre-WXT paths are gone, every
 * path a package script points at is tracked, and the build validation hook is
 * still wired into `wxt.config.ts`.
 *
 * Run with `npm run verify:repository`.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export const FORBIDDEN_PATHS: readonly string[] = [
  '.agents/',
  '.claude/',
  '.github/agents/',
  '.serena/',
  '.wxt/',
  '.output/',
  'agent.md',
  'skills-lock.json',
  'shopee-analyzer/',
  'website/',
  'docs/superpowers/',
  'coverage/',
  'dist/',
  'test-results/',
  'playwright-report/',
  'backend/models_cache/',
  'backend/test_output.png',
  'tests/fixtures/',
  'tests/mocks/msw-handlers.js',
  'test_run.py',
  'artifacts/',
  'extension/_metadata/',
];

/**
 * Paths from the pre-WXT layout (hand-written manifest, importScripts loader,
 * flat backend package, Node CommonJS build scripts). They must not come back.
 */
export const LEGACY_PATHS: readonly string[] = [
  'extension/manifest.json',
  'extension/background/service-worker.js',
  'extension/popup/popup.html',
  'extension/popup/popup.js',
  'extension/popup/popup.css',
  'extension/options/options.html',
  'extension/options/options.js',
  'extension/options/options.css',
  'extension/icons/',
  'extension/fonts/',
  'extension/rules/',
  'backend/main.py',
  'backend/pipeline.py',
  'backend/models/',
  'backend/utils/',
  'backend/comfy_lanpaint/',
  'scripts/build.js',
  'scripts/package.js',
  'scripts/validate-extension.js',
  'scripts/repository-check.js',
  'scripts/launch-extension.js',
  'scripts/backend-check.js',
  'scripts/popup-smoke.js',
  'scripts/real-url-smoke.js',
  'scripts/prepare-bookwalker-profile.js',
  'playwright.config.js',
];

// Paths that tooling can create; if present they must be ignored by Git.
const GENERATED_PATHS: readonly string[] = [
  'coverage/index.html',
  'dist/chrome-mv3/manifest.json',
  '.wxt/tsconfig.json',
  '.output/chrome-mv3/manifest.json',
  'test-results/e2e-output.txt',
  'playwright-report/index.html',
  'backend/models_cache/model.bin',
  'backend/test_output.png',
  'backend/src/manga_backend/__pycache__/main.cpython-312.pyc',
  'backend/src/manga_backend/models/__pycache__/detector.cpython-312.pyc',
  'shopee-analyzer/data/sample.json',
  'shopee-analyzer/exports/report.csv',
];

export const REQUIRED_PATHS: readonly string[] = [
  // WXT project
  'wxt.config.ts',
  'tsconfig.json',
  'biome.json',
  'vitest.config.ts',
  'playwright.config.ts',
  // Extension sources (WXT srcDir)
  'extension/entrypoints/background.ts',
  'extension/entrypoints/content/index.ts',
  'extension/entrypoints/popup/index.html',
  'extension/entrypoints/options/index.html',
  'extension/lib/api.ts',
  'extension/lib/cache.ts',
  'extension/lib/font-registry.ts',
  'extension/lib/settings.ts',
  'extension/lib/utils.ts',
  'extension/shared',
  // Shipped static assets (copied verbatim into dist/chrome-mv3)
  'extension/public/icons/icon16.png',
  'extension/public/icons/icon48.png',
  'extension/public/icons/icon128.png',
  'extension/public/fonts/CCWildWords.ttf',
  'extension/public/rules/bookwalker-cors.json',
  // Icon sources
  'extension/assets/icons',
  // Backend (src layout)
  'backend/pyproject.toml',
  'backend/uv.lock',
  'backend/start.ps1',
  'backend/src/manga_backend/main.py',
  'backend/src/manga_backend/api',
  'backend/src/manga_backend/pipeline',
  'backend/src/manga_backend/pipeline/orchestrator.py',
  'backend/src/manga_backend/models',
  'backend/src/manga_backend/models/animetext_yolo_detector.py',
  'backend/src/manga_backend/models/flux_klein_inpainter.py',
  'backend/src/manga_backend/models/flux_lanpaint_inpainter.py',
  'backend/src/manga_backend/comfy_lanpaint',
  'backend/src/manga_backend/utils',
  'backend/src/manga_backend/fonts/CCWildWords.ttf',
  'backend/tests',
  // Scripts and tests
  'scripts/validate-extension.ts',
  'scripts/repository-check.ts',
  'scripts/bookwalker-real-uat.ps1',
  'scripts/prepare-bookwalker-profile.ts',
  'scripts/popup-smoke.ts',
  'scripts/real-url-smoke.ts',
  'tests/unit',
  'tests/integration',
  'tests/e2e',
];

const REQUIRED_PACKAGE_SCRIPTS: readonly string[] = [
  'build',
  'zip',
  'validate',
  'test',
  'test:e2e',
  'verify',
  'verify:repository',
];

export function normalizePath(filePath: unknown): string {
  return String(filePath ?? '')
    .replace(/\\/g, '/')
    .replace(/^\.\//, '');
}

function matchesPattern(normalized: string, patterns: readonly string[]): boolean {
  return patterns.some((pattern) =>
    pattern.endsWith('/')
      ? normalized === pattern.slice(0, -1) || normalized.startsWith(pattern)
      : normalized === pattern,
  );
}

export function isForbiddenPath(filePath: string): boolean {
  const normalized = normalizePath(filePath);
  return (
    matchesPattern(normalized, FORBIDDEN_PATHS) ||
    normalized.endsWith('.pyc') ||
    normalized.split('/').includes('__pycache__')
  );
}

export function isLegacyPath(filePath: string): boolean {
  return matchesPattern(normalizePath(filePath), LEGACY_PATHS);
}

export function findForbiddenPaths(filePaths: readonly string[]): string[] {
  return [...new Set(filePaths.map(normalizePath).filter(isForbiddenPath))].sort();
}

export function findLegacyPaths(filePaths: readonly string[]): string[] {
  return [...new Set(filePaths.map(normalizePath).filter(isLegacyPath))].sort();
}

function git(repoRoot: string, args: string[], input?: string): string {
  return execFileSync('git', args, {
    cwd: repoRoot,
    encoding: 'utf8',
    input,
    stdio: ['pipe', 'pipe', 'pipe'],
    maxBuffer: 64 * 1024 * 1024,
  });
}

function gitFiles(repoRoot: string, args: string[]): string[] {
  return git(repoRoot, args).split('\0').filter(Boolean).map(normalizePath);
}

function gitIgnoredSet(repoRoot: string, relativePaths: readonly string[]): Set<string> {
  const paths = [...new Set(relativePaths.map(normalizePath))];
  if (paths.length === 0) return new Set();
  try {
    const output = git(repoRoot, ['check-ignore', '--stdin', '-z'], `${paths.join('\0')}\0`);
    return new Set(output.split('\0').filter(Boolean).map(normalizePath));
  } catch (error) {
    // check-ignore exits 1 when nothing matched.
    if ((error as { status?: number }).status === 1) return new Set();
    throw error;
  }
}

interface PackageJson {
  scripts?: Record<string, unknown>;
}

export function packageScriptTargets(packageJson: PackageJson | null): string[] {
  const targets = new Set<string>();
  const commands =
    packageJson?.scripts && typeof packageJson.scripts === 'object'
      ? Object.values(packageJson.scripts)
      : [];
  for (const command of commands) {
    if (typeof command !== 'string') continue;
    for (const rawToken of command.split(/\s+/)) {
      const candidate = rawToken.replace(/^['"`]|['"`,;]$/g, '');
      if (!candidate) continue;
      if (/^(?:\.?\.?[\\/])?(?:extension|tests|scripts|backend)[\\/]/.test(candidate)) {
        targets.add(normalizePath(candidate));
      }
    }
  }
  return [...targets].sort();
}

function readPackageJson(repoRoot: string): PackageJson | null {
  try {
    return JSON.parse(fs.readFileSync(path.join(repoRoot, 'package.json'), 'utf8')) as PackageJson;
  } catch {
    return null;
  }
}

function escapeRegex(char: string): string {
  return char.replace(/[\\^$+.()|[\]]/g, '\\$&');
}

function expandTarget(repoRoot: string, target: string): string[] {
  const normalized = normalizePath(target);
  if (!/[?*{[]/.test(normalized)) return [normalized];

  let pattern = '';
  for (let index = 0; index < normalized.length; index += 1) {
    const char = normalized.charAt(index);
    if (char === '*' && normalized[index + 1] === '*' && normalized[index + 2] === '/') {
      pattern += '(?:.*/)?';
      index += 2;
    } else if (char === '*' && normalized[index + 1] === '*') {
      pattern += '.*';
      index += 1;
    } else if (char === '*') {
      pattern += '[^/]*';
    } else if (char === '?') {
      pattern += '[^/]';
    } else if (char === '{') {
      const end = normalized.indexOf('}', index);
      if (end > index) {
        const alternatives = normalized
          .slice(index + 1, end)
          .split(',')
          .map(escapeRegex);
        pattern += `(${alternatives.join('|')})`;
        index = end;
      } else {
        pattern += '\\{';
      }
    } else {
      pattern += escapeRegex(char);
    }
  }

  const regex = new RegExp(`^${pattern}$`);
  const results: string[] = [];
  const wildcardIndex = normalized.search(/[?*{[]/);
  const prefix = wildcardIndex >= 0 ? normalized.slice(0, wildcardIndex) : normalized;
  const base = prefix.includes('/') ? prefix.slice(0, prefix.lastIndexOf('/')) : '';

  const walk = (current: string, relative: string) => {
    if (!fs.existsSync(current)) return;
    if (fs.statSync(current).isFile()) {
      if (regex.test(normalizePath(relative))) results.push(normalizePath(relative));
      return;
    }
    for (const entry of fs.readdirSync(current)) {
      walk(path.join(current, entry), relative ? `${relative}/${entry}` : entry);
    }
  };
  walk(path.join(repoRoot, base), base);
  return results.sort();
}

export function validateTrackedTargets(
  repoRoot: string,
  targets: readonly string[],
  tracked: readonly string[],
  label: string,
  basePrefix = '',
): string[] {
  const errors: string[] = [];
  const trackedSet = new Set(tracked);
  const ignored = gitIgnoredSet(
    repoRoot,
    targets.flatMap((target) => expandTarget(repoRoot, target)),
  );
  for (const target of targets) {
    const expanded = expandTarget(repoRoot, target);
    if (expanded.length === 0) {
      errors.push(`${label} target does not exist: ${target}`);
      continue;
    }
    for (const relativePath of expanded) {
      const absolute = path.join(repoRoot, relativePath);
      if (!fs.existsSync(absolute)) {
        errors.push(`${label} target does not exist: ${relativePath}`);
      } else if (fs.statSync(absolute).isDirectory()) {
        if (!tracked.some((filePath) => filePath.startsWith(`${relativePath}/`))) {
          errors.push(`${label} target is not Git-tracked: ${relativePath}`);
        }
      } else if (!trackedSet.has(`${basePrefix}${relativePath}`)) {
        errors.push(`${label} target is not Git-tracked: ${relativePath}`);
      }
      if (ignored.has(normalizePath(relativePath))) {
        errors.push(`${label} target is Git-ignored: ${relativePath}`);
      }
    }
  }
  return errors;
}

function bulletList(items: readonly string[]): string {
  return items.map((item) => `- ${item}`).join('\n');
}

function checkBuildHookWiring(repoRoot: string): string[] {
  const configPath = path.join(repoRoot, 'wxt.config.ts');
  if (!fs.existsSync(configPath)) return [];
  const source = fs.readFileSync(configPath, 'utf8');
  if (!source.includes('build:done') || !source.includes('validate-extension')) {
    return [
      'wxt.config.ts must run scripts/validate-extension.ts from a `build:done` hook so every build is validated',
    ];
  }
  return [];
}

export function checkRepository(repoRoot: string = REPO_ROOT): string[] {
  const errors: string[] = [];
  const tracked = gitFiles(repoRoot, ['ls-files', '-z']);
  const existingTracked = tracked.filter((filePath) =>
    fs.existsSync(path.join(repoRoot, filePath)),
  );
  const untracked = gitFiles(repoRoot, ['ls-files', '--others', '--exclude-standard', '-z']).filter(
    (filePath) => fs.existsSync(path.join(repoRoot, filePath)),
  );
  const gitVisible = [...existingTracked, ...untracked];

  const forbidden = findForbiddenPaths(gitVisible);
  if (forbidden.length > 0) {
    errors.push(`Forbidden Git-visible paths:\n${bulletList(forbidden)}`);
  }

  const legacy = findLegacyPaths(gitVisible);
  if (legacy.length > 0) {
    errors.push(
      `Legacy pre-WXT paths still present (replaced by the WXT build):\n${bulletList(legacy)}`,
    );
  }

  const existingGenerated = GENERATED_PATHS.filter((relativePath) =>
    fs.existsSync(path.join(repoRoot, relativePath)),
  );
  const ignoredGenerated = gitIgnoredSet(repoRoot, existingGenerated);
  const notIgnored = existingGenerated.filter(
    (relativePath) => !ignoredGenerated.has(relativePath),
  );
  if (notIgnored.length > 0) {
    errors.push(`Generated paths not ignored:\n${bulletList(notIgnored)}`);
  }

  const missing = REQUIRED_PATHS.filter(
    (relativePath) => !fs.existsSync(path.join(repoRoot, relativePath)),
  );
  if (missing.length > 0) {
    errors.push(`Missing required paths:\n${bulletList(missing)}`);
  }

  const packageJson = readPackageJson(repoRoot);
  if (!packageJson) {
    errors.push('Missing or invalid package.json');
  } else {
    const missingScripts = REQUIRED_PACKAGE_SCRIPTS.filter(
      (name) => typeof packageJson.scripts?.[name] !== 'string',
    );
    if (missingScripts.length > 0) {
      errors.push(`package.json is missing scripts:\n${bulletList(missingScripts)}`);
    }
    errors.push(
      ...validateTrackedTargets(
        repoRoot,
        packageScriptTargets(packageJson),
        tracked,
        'package script',
      ),
    );
  }

  errors.push(...checkBuildHookWiring(repoRoot));
  return errors;
}

function main(): void {
  try {
    const errors = checkRepository();
    if (errors.length > 0) {
      console.error(errors.join('\n\n'));
      process.exitCode = 1;
    } else {
      console.log('Repository boundary validation passed.');
    }
  } catch (error) {
    console.error(`Repository validation failed: ${(error as Error).message}`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
