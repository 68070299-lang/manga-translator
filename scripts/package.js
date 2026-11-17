const fs = require('fs');
const path = require('path');
const { EXTENSION_DIR, validateExtension } = require('./validate-extension');

const DIST_ROOT = path.join(__dirname, '..', 'dist');

function formatValidationErrors(result) {
  return (result.errors || []).map(error => `- ${error}`).join('\n');
}

function packageExtension({
  extensionDir = EXTENSION_DIR,
  distRoot = DIST_ROOT,
  fileSystem = fs,
  validate = validateExtension
} = {}) {
  const packageDir = path.join(distRoot, 'extension');
  const stagingDir = path.join(distRoot, '.extension-staging');
  const backupDir = path.join(distRoot, '.extension-backup');

  const sourceResult = validate(extensionDir);
  if (!sourceResult.ok) {
    throw new Error(`Source extension validation failed:\n${formatValidationErrors(sourceResult)}`);
  }

  fileSystem.mkdirSync(distRoot, { recursive: true });
  fileSystem.rmSync(stagingDir, { recursive: true, force: true });
  // A backup left by an interrupted previous run is not a package candidate.
  fileSystem.rmSync(backupDir, { recursive: true, force: true });
  fileSystem.mkdirSync(stagingDir, { recursive: true });

  for (const relativePath of new Set(['manifest.json', ...(sourceResult.files || [])])) {
    const sourcePath = path.join(extensionDir, relativePath);
    const targetPath = path.join(stagingDir, relativePath);
    fileSystem.mkdirSync(path.dirname(targetPath), { recursive: true });
    fileSystem.copyFileSync(sourcePath, targetPath);
  }

  const stagedResult = validate(stagingDir);
  if (!stagedResult.ok) {
    fileSystem.rmSync(stagingDir, { recursive: true, force: true });
    throw new Error(`Packaged extension validation failed:\n${formatValidationErrors(stagedResult)}`);
  }

  let hadExistingPackage = false;
  try {
    hadExistingPackage = fileSystem.existsSync(packageDir);
    if (hadExistingPackage) fileSystem.renameSync(packageDir, backupDir);
    fileSystem.renameSync(stagingDir, packageDir);
    fileSystem.rmSync(backupDir, { recursive: true, force: true });
    return packageDir;
  } catch (error) {
    fileSystem.rmSync(stagingDir, { recursive: true, force: true });
    if (hadExistingPackage && fileSystem.existsSync(backupDir) && !fileSystem.existsSync(packageDir)) {
      fileSystem.renameSync(backupDir, packageDir);
    }
    throw error;
  }
}

if (require.main === module) {
  try {
    const packageDir = packageExtension();
    console.log('Packaged extension staged at:', path.resolve(packageDir));
    console.log('Zip the contents of dist/extension for browser-store distribution.');
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}

module.exports = { packageExtension };
