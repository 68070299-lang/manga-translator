const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { validateExtension } = require('./validate-extension');

const FORBIDDEN_PATHS = [
  '.agents/',
  '.claude/',
  '.github/agents/',
  '.serena/',
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

const GENERATED_PATHS = [
  'coverage/index.html',
  'dist/extension/manifest.json',
  'test-results/e2e-output.txt',
  'playwright-report/index.html',
  'backend/models_cache/model.bin',
  'backend/test_output.png',
  'backend/__pycache__/main.cpython-312.pyc',
  'backend/models/__pycache__/detector.cpython-312.pyc',
  'shopee-analyzer/data/sample.json',
  'shopee-analyzer/exports/report.csv'
];

const REQUIRED_PATHS = [
  'extension/manifest.json',
  'extension/background/service-worker.js',
  'extension/content/content.js',
  'extension/content/content-helpers.js',
  'extension/content/content.css',
  'extension/lib/api.js',
  'extension/lib/cache.js',
  'extension/lib/font-registry.js',
  'extension/lib/overlay-dom.js',
  'extension/lib/region-capture.js',
  'extension/lib/settings.js',
  'extension/lib/translated-image.js',
  'extension/lib/utils.js',
  'extension/popup/popup.html',
  'extension/options/options.html',
  'extension/icons/icon16.png',
  'extension/icons/icon48.png',
  'extension/icons/icon128.png',
  'extension/fonts/CCWildWords.ttf',
  'backend/main.py',
  'backend/pipeline.py',
  'backend/pyproject.toml',
  'backend/uv.lock',
  'backend/models',
  'backend/models/animetext_yolo_detector.py',
  'backend/models/flux_klein_inpainter.py',
  'backend/models/flux_lanpaint_inpainter.py',
  'backend/comfy_lanpaint',
  'backend/utils',
  'backend/fonts/CCWildWords.ttf',
  'scripts/build.js',
  'scripts/package.js',
  'scripts/validate-extension.js',
  'scripts/bookwalker-real-uat.ps1',
  'scripts/prepare-bookwalker-profile.js',
  'tests/unit',
  'tests/integration',
  'tests/e2e'
];

// Git-visible extension files that are legitimately not reachable from the manifest.
const EXTENSION_ALLOWED_PATTERNS = [
  /^extension\/icons\/[^/]+\.svg$/,
  /^extension\/icons\/generate-icons\.html$/,
  /^extension\/rules\/[^/]+\.json$/
];

function normalizePath(filePath) {
  return String(filePath || '').replace(/\\/g, '/').replace(/^\.\//, '');
}

function isForbiddenPath(filePath) {
  const normalized = normalizePath(filePath);
  return FORBIDDEN_PATHS.some(pattern => (
    pattern.endsWith('/')
      ? normalized === pattern.slice(0, -1) || normalized.startsWith(pattern)
      : normalized === pattern
  )) || normalized.endsWith('.pyc') || normalized.split('/').includes('__pycache__');
}

function findForbiddenPaths(filePaths) {
  return [...new Set(filePaths.map(normalizePath).filter(isForbiddenPath))].sort();
}

function findUnreferencedExtensionFiles(filePaths, referencedFiles) {
  const referenced = new Set(['extension/manifest.json', ...referencedFiles.map(item => `extension/${normalizePath(item)}`)]);
  return [...new Set(filePaths.map(normalizePath).filter(filePath => (
    filePath.startsWith('extension/')
    && !referenced.has(filePath)
    && !EXTENSION_ALLOWED_PATTERNS.some(pattern => pattern.test(filePath))
  )))].sort();
}

function gitFiles(repoRoot, args) {
  const output = execFileSync('git', args, {
    cwd: repoRoot,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe']
  });
  return output.split('\0').filter(Boolean).map(normalizePath);
}

function gitIgnoredSet(repoRoot, relativePaths) {
  const paths = [...new Set(relativePaths.map(normalizePath))];
  if (paths.length === 0) return new Set();
  try {
    const output = execFileSync('git', ['check-ignore', '--stdin', '-z'], {
      cwd: repoRoot,
      input: `${paths.join('\0')}\0`,
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'pipe']
    });
    return new Set(output.split('\0').filter(Boolean).map(normalizePath));
  } catch (error) {
    if (error.status === 1) return new Set();
    throw error;
  }
}

function packageScriptTargets(packageJson) {
  const targets = new Set();
  const scripts = packageJson && packageJson.scripts && typeof packageJson.scripts === 'object'
    ? Object.values(packageJson.scripts)
    : [];
  for (const command of scripts) {
    if (typeof command !== 'string') continue;
    for (const rawToken of command.split(/\s+/)) {
      const candidate = rawToken.replace(/^['"`]|['"`,;]$/g, '');
      if (!candidate) continue;
      if (/^(?:\.?\.?[\\/])?(?:extension|tests|scripts|backend)[\\/]/.test(candidate)
        || /^(?:launch-extension|test-extension|test-mcp)\.js$/.test(candidate)) {
        targets.add(normalizePath(candidate));
      }
    }
  }
  return [...targets].sort();
}

function readPackageJson(repoRoot) {
  try {
    return JSON.parse(fs.readFileSync(path.join(repoRoot, 'package.json'), 'utf8'));
  } catch (_error) {
    return null;
  }
}

function expandTarget(repoRoot, target) {
  const normalized = normalizePath(target);
  if (!/[?*{[]/.test(normalized)) return [normalized];
  let pattern = '';
  for (let index = 0; index < normalized.length; index += 1) {
    const char = normalized[index];
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
        pattern += `(${normalized.slice(index + 1, end).split(',').map(part => part.replace(/[\\^$+.()|[\]]/g, '\\$&')).join('|')})`;
        index = end;
      } else {
        pattern += '\\{';
      }
    } else {
      pattern += char.replace(/[\\^$+.()|[\]]/g, '\\$&');
    }
  }
  const regex = new RegExp(`^${pattern}$`);
  const results = [];
  const wildcardIndex = normalized.search(/[?*{[]/);
  const prefix = wildcardIndex >= 0 ? normalized.slice(0, wildcardIndex) : normalized;
  const base = prefix.includes('/') ? prefix.slice(0, prefix.lastIndexOf('/')) : '';
  const start = path.join(repoRoot, base);
  function walk(current, relative) {
    if (!fs.existsSync(current)) return;
    const stat = fs.statSync(current);
    if (stat.isFile()) {
      if (regex.test(normalizePath(relative))) results.push(normalizePath(relative));
      return;
    }
    for (const entry of fs.readdirSync(current)) {
      walk(path.join(current, entry), relative ? `${relative}/${entry}` : entry);
    }
  }
  walk(start, base);
  return results.sort();
}

function validateTrackedTargets(repoRoot, targets, tracked, label, basePrefix = '') {
  const errors = [];
  const trackedSet = new Set(tracked);
  const expandedTargets = targets.flatMap(target => expandTarget(repoRoot, target));
  const ignored = gitIgnoredSet(repoRoot, expandedTargets);
  for (const target of targets) {
    const expanded = expandTarget(repoRoot, target);
    if (expanded.length === 0) {
      errors.push(`${label} target does not exist: ${target}`);
      continue;
    }
    for (const relativePath of expanded) {
      if (!fs.existsSync(path.join(repoRoot, relativePath))) {
        errors.push(`${label} target does not exist: ${relativePath}`);
      } else if (fs.statSync(path.join(repoRoot, relativePath)).isDirectory()) {
        if (!tracked.some(filePath => filePath.startsWith(`${relativePath}/`))) {
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

function checkRepository(repoRoot = path.resolve(__dirname, '..')) {
  const errors = [];
  const tracked = gitFiles(repoRoot, ['ls-files', '-z']);
  const existingTracked = tracked.filter(filePath => fs.existsSync(path.join(repoRoot, filePath)));
  const untracked = gitFiles(repoRoot, ['ls-files', '--others', '--exclude-standard', '-z'])
    .filter(filePath => fs.existsSync(path.join(repoRoot, filePath)));
  const gitVisible = [...existingTracked, ...untracked];
  const offenders = findForbiddenPaths(gitVisible);
  if (offenders.length > 0) {
    errors.push(`Forbidden Git-visible paths:\n${offenders.map(item => `- ${item}`).join('\n')}`);
  }

  const existingGenerated = GENERATED_PATHS.filter(relativePath =>
    fs.existsSync(path.join(repoRoot, relativePath))
  );
  const ignoredGenerated = gitIgnoredSet(repoRoot, existingGenerated);
  const notIgnored = existingGenerated.filter(relativePath => !ignoredGenerated.has(relativePath));
  if (notIgnored.length > 0) {
    errors.push(`Generated paths not ignored:\n${notIgnored.map(item => `- ${item}`).join('\n')}`);
  }

  const missing = REQUIRED_PATHS.filter(relativePath => !fs.existsSync(path.join(repoRoot, relativePath)));
  if (missing.length > 0) {
    errors.push(`Missing required paths:\n${missing.map(item => `- ${item}`).join('\n')}`);
  }

  const packageJson = readPackageJson(repoRoot);
  if (!packageJson) {
    errors.push('Missing or invalid package.json');
  } else {
    errors.push(...validateTrackedTargets(
      repoRoot,
      packageScriptTargets(packageJson),
      tracked,
      'package script'
    ));
  }

  const extensionResult = validateExtension(path.join(repoRoot, 'extension'));
  if (!extensionResult.ok) {
    errors.push(`Extension validation failed:\n${extensionResult.errors.map(item => `- ${item}`).join('\n')}`);
  }
  if (extensionResult.ok) {
    const unreferenced = findUnreferencedExtensionFiles(gitVisible, extensionResult.files);
    if (unreferenced.length > 0) {
      errors.push(`Git-visible extension files not referenced by the manifest or allowlist:\n${unreferenced.map(item => `- ${item}`).join('\n')}`);
    }
  }
  errors.push(...validateTrackedTargets(
    repoRoot,
    ['extension/manifest.json', ...extensionResult.files.map(filePath => `extension/${filePath}`)],
    tracked,
    'extension manifest'
  ));
  return errors;
}

if (require.main === module) {
  try {
    const errors = checkRepository();
    if (errors.length > 0) {
      console.error(errors.join('\n\n'));
      process.exitCode = 1;
    } else {
      console.log('Repository boundary validation passed.');
    }
  } catch (error) {
    console.error(`Repository validation failed: ${error.message}`);
    process.exitCode = 1;
  }
}

module.exports = {
  FORBIDDEN_PATHS,
  findForbiddenPaths,
  findUnreferencedExtensionFiles,
  REQUIRED_PATHS,
  isForbiddenPath,
  normalizePath,
  packageScriptTargets,
  validateTrackedTargets,
  checkRepository
};
