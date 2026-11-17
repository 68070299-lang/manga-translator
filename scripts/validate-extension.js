const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const EXTENSION_DIR = path.join(__dirname, '..', 'extension');

function readJson(filePath, errors) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch (error) {
    errors.push(`${path.relative(process.cwd(), filePath)} is not valid JSON: ${error.message}`);
    return null;
  }
}

function addFile(files, relativePath) {
  if (!relativePath || /^([a-z]+:)?\/\//i.test(relativePath) || relativePath.startsWith('#')) {
    return;
  }

  const cleanPath = relativePath.split(/[?#]/)[0];
  if (!cleanPath) return;
  files.add(cleanPath.replace(/\\/g, '/'));
}

function addManifestFile(files, relativePath, errors) {
  if (!relativePath || typeof relativePath !== 'string') return;
  if (/^([a-z]+:)?\/\//i.test(relativePath)) {
    errors.push(`manifest.json must reference a local extension asset: ${relativePath}`);
    return;
  }
  const cleanPath = relativePath.split(/[?#]/)[0].replace(/\\/g, '/');
  const normalized = path.posix.normalize(cleanPath);
  if (normalized === '..' || normalized.startsWith('../')) {
    errors.push(`manifest.json references asset outside extension/: ${relativePath}`);
    return;
  }
  files.add(normalized);
}

function addHtmlReferences(files, extensionDir, htmlRelativePath, errors) {
  const htmlPath = path.join(extensionDir, htmlRelativePath);
  if (!fs.existsSync(htmlPath)) return;

  const html = fs.readFileSync(htmlPath, 'utf8');
  const assetPattern = /\b(?:src|href)=["']([^"']+)["']/gi;
  let match;
  while ((match = assetPattern.exec(html)) !== null) {
    const asset = match[1];
    if (
      asset.startsWith('#') ||
      /^https?:\/\//i.test(asset) ||
      /^data:/i.test(asset) ||
      /^mailto:/i.test(asset)
    ) {
      continue;
    }

    const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(htmlRelativePath), asset));
    if (resolved.startsWith('..')) {
      errors.push(`${htmlRelativePath} references asset outside extension/: ${asset}`);
      continue;
    }
    addFile(files, resolved);
  }
}

function addCssReferences(files, extensionDir, cssRelativePath, errors) {
  const cssPath = path.join(extensionDir, cssRelativePath);
  if (!fs.existsSync(cssPath)) return;

  const css = fs.readFileSync(cssPath, 'utf8');
  const assetPattern = /url\(\s*["']?([^"')]+)["']?\s*\)/gi;
  let match;
  while ((match = assetPattern.exec(css)) !== null) {
    const asset = match[1].trim();
    if (!asset || /^(?:data|https?|blob):/i.test(asset)) continue;
    const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(cssRelativePath), asset));
    if (resolved === '..' || resolved.startsWith('../')) {
      errors.push(`${cssRelativePath} references asset outside extension/: ${asset}`);
      continue;
    }
    addFile(files, resolved);
  }
}

function addImportScripts(files, extensionDir, scriptRelativePath, errors) {
  const scriptPath = path.join(extensionDir, scriptRelativePath);
  if (!fs.existsSync(scriptPath)) return;

  const source = fs.readFileSync(scriptPath, 'utf8');
  const importScriptsPattern = /importScripts\(([^)]+)\)/g;
  let importMatch;
  while ((importMatch = importScriptsPattern.exec(source)) !== null) {
    const args = importMatch[1];
    const stringPattern = /["']([^"']+)["']/g;
    let stringMatch;
    while ((stringMatch = stringPattern.exec(args)) !== null) {
      const resolved = path.posix.normalize(
        path.posix.join(path.posix.dirname(scriptRelativePath), stringMatch[1])
      );
      if (resolved.startsWith('..')) {
        errors.push(`${scriptRelativePath} imports asset outside extension/: ${stringMatch[1]}`);
        continue;
      }
      addFile(files, resolved);
    }
  }
}

function collectManifestFiles(manifest, extensionDir, errors) {
  const files = new Set();

  const addManifestIcons = icons => {
    if (!icons || typeof icons !== 'object') return;
    Object.values(icons).forEach(iconPath => addManifestFile(files, iconPath, errors));
  };
  addManifestIcons(manifest.icons);
  addManifestIcons(manifest.action?.default_icon);
  addManifestFile(files, manifest.action?.default_popup, errors);
  addManifestFile(files, manifest.options_page, errors);
  addManifestFile(files, manifest.background?.service_worker, errors);

  for (const contentScript of manifest.content_scripts || []) {
    for (const jsPath of contentScript.js || []) addManifestFile(files, jsPath, errors);
    for (const cssPath of contentScript.css || []) addManifestFile(files, cssPath, errors);
  }

  for (const resourceGroup of manifest.web_accessible_resources || []) {
    for (const resourcePath of resourceGroup.resources || []) addManifestFile(files, resourcePath, errors);
  }

  for (const ruleResource of manifest.declarative_net_request?.rule_resources || []) {
    addManifestFile(files, ruleResource.path, errors);
  }

  const visited = new Set();
  const pending = [...files];
  while (pending.length > 0) {
    const filePath = pending.shift();
    if (visited.has(filePath)) continue;
    visited.add(filePath);
    const before = new Set(files);
    if (filePath.endsWith('.html')) {
      addHtmlReferences(files, extensionDir, filePath, errors);
    }
    if (filePath.endsWith('.js')) {
      addImportScripts(files, extensionDir, filePath, errors);
    }
    if (filePath.endsWith('.css')) {
      addCssReferences(files, extensionDir, filePath, errors);
    }
    for (const discovered of files) {
      if (!before.has(discovered)) pending.push(discovered);
    }
  }

  return Array.from(files).sort();
}

// Fonts are read at runtime rather than referenced by the manifest, so packaging
// only includes them through this list.
function addBundledFonts(files, extensionDir, errors) {
  const registryPath = path.join(extensionDir, 'lib', 'font-registry.js');
  if (!files.has('lib/font-registry.js') || !fs.existsSync(registryPath)) return;

  const { BUNDLED_FONTS } = require(registryPath);
  for (const font of BUNDLED_FONTS) {
    addManifestFile(files, font.file, errors);
    const fontPath = path.join(extensionDir, font.file);
    if (!fs.existsSync(fontPath)) continue;
    const sha256 = crypto.createHash('sha256').update(fs.readFileSync(fontPath)).digest('hex');
    if (sha256 !== font.sha256) {
      errors.push(`Bundled font does not match its registered checksum: ${font.file}`);
    }
  }
}

function validateExtension(extensionDir = EXTENSION_DIR) {
  const errors = [];
  const manifestPath = path.join(extensionDir, 'manifest.json');

  if (!fs.existsSync(manifestPath)) {
    return {
      ok: false,
      files: [],
      errors: [`Missing manifest: ${path.relative(process.cwd(), manifestPath)}`]
    };
  }

  const manifest = readJson(manifestPath, errors);
  if (!manifest) {
    return { ok: false, files: [], errors };
  }

  if (manifest.manifest_version !== 3) {
    errors.push('manifest.json must declare manifest_version 3');
  }
  if (!manifest.name) {
    errors.push('manifest.json must declare name');
  }
  if (!manifest.version) {
    errors.push('manifest.json must declare version');
  }

  const fileSet = new Set(collectManifestFiles(manifest, extensionDir, errors));
  addBundledFonts(fileSet, extensionDir, errors);
  const files = Array.from(fileSet).sort();
  const extensionRealPath = fs.realpathSync(extensionDir);
  for (const filePath of files) {
    const absolutePath = path.join(extensionDir, filePath);
    if (!fs.existsSync(absolutePath)) {
      errors.push(`Missing extension file referenced by manifest or HTML: ${filePath}`);
      continue;
    }
    let realPath;
    try {
      realPath = fs.realpathSync(absolutePath);
    } catch (_error) {
      continue;
    }
    if (realPath !== extensionRealPath && !realPath.startsWith(`${extensionRealPath}${path.sep}`)) {
      errors.push(`Referenced extension path escapes extension/ through symlink: ${filePath}`);
      continue;
    }
    if (!fs.statSync(absolutePath).isFile()) {
      errors.push(`Referenced extension path is not a file: ${filePath}`);
    }
  }

  return {
    ok: errors.length === 0,
    files,
    errors
  };
}

module.exports = {
  EXTENSION_DIR,
  validateExtension
};
