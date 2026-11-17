const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const repoRoot = path.resolve(__dirname, '..');
const backendRoot = path.join(repoRoot, 'backend');
const testTempRoot = path.join(repoRoot, 'test-results', 'backend-tmp');
const uvCacheRoot = path.join(testTempRoot, 'uv-cache');
fs.mkdirSync(testTempRoot, { recursive: true });

const mode = process.argv[2] || 'test';
const forwardedArgs = process.argv.slice(3);
const commands = {
  test: ['-m', 'unittest', 'discover', '-s', 'backend', '-p', 'test_*.py'],
  lint: ['-m', 'ruff', 'check', 'backend', 'scripts/backend-smoke.py'],
  compile: ['-m', 'compileall', '-q', 'backend', 'scripts/backend-smoke.py'],
  smoke: ['scripts/backend-smoke.py', ...forwardedArgs]
};

const pythonArgs = commands[mode];

if (!pythonArgs) {
  console.error(`Unknown backend check "${mode}". Expected one of: ${Object.keys(commands).join(', ')}`);
  process.exit(2);
}

function uvCandidate() {
  return {
    command: 'uv',
    args: ['run', '--project', backendRoot, '--python', '3.12', '--locked', 'python'],
    env: {}
  };
}

function pythonCandidates() {
  const candidates = [];

  if (process.env.PYTHON) {
    candidates.push({ command: process.env.PYTHON, args: [], env: {} });
  }

  candidates.push(uvCandidate());

  candidates.push({ command: 'python', args: [], env: {} });
  if (process.platform === 'win32') {
    candidates.push({ command: 'py', args: ['-3.12'], env: {} });
  } else {
    candidates.push({ command: 'python3', args: [], env: {} });
  }

  return candidates;
}

function commandExists(candidate) {
  return !candidate.command.includes(path.sep) || fs.existsSync(candidate.command);
}

let lastError = null;

for (const candidate of pythonCandidates()) {
  if (!commandExists(candidate)) {
    continue;
  }

  const result = spawnSync(candidate.command, [...candidate.args, ...pythonArgs], {
    cwd: repoRoot,
    env: {
      ...process.env,
      ...candidate.env,
      TEMP: testTempRoot,
      TMP: testTempRoot,
      PYTHONDONTWRITEBYTECODE: '1',
      MANGA_TRANSLATOR_TESTING: mode === 'test' ? '1' : '',
      UV_CACHE_DIR: uvCacheRoot
    },
    stdio: 'inherit'
  });

  if (result.error && result.error.code === 'ENOENT') {
    lastError = result.error;
    continue;
  }

  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }

  process.exit(result.status ?? 1);
}

console.error(`Unable to find uv or a Python interpreter for backend ${mode}.`);
if (lastError) {
  console.error(lastError.message);
}
process.exit(1);
