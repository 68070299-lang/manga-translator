/**
 * Validates the built extension (`dist/chrome-mv3`, produced by `wxt build`).
 *
 * Checks, in order:
 *  - the generated manifest is MV3 and stays inside the permission budget
 *    (no `tabs`, `scripting`, or `activeTab`);
 *  - every file the manifest references exists, including files reachable
 *    from referenced HTML (`src`/`href`), CSS (`url()`), and JS
 *    (`importScripts` and relative imports);
 *  - bundled fonts exist and match the sha256 in `extension/lib/font-registry.ts`;
 *  - the declarativeNetRequest rule resources are present and are JSON arrays;
 *  - no remote code: no remote scripts or imports, no inline scripts, and no
 *    remote or `unsafe-*` sources in the extension CSP.
 *
 * Runs automatically after every `wxt build`/`wxt zip` through the
 * `build:done` hook in `wxt.config.ts`, and can be run on its own with
 * `npm run validate`.
 */
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { BundledFont } from '../extension/lib/font-registry.ts';
import { BUNDLED_FONTS } from '../extension/lib/font-registry.ts';

export const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const DIST_DIR = path.join(REPO_ROOT, 'dist', 'chrome-mv3');

/** Permissions the extension may request. Widening this is a deliberate change. */
export const PERMISSION_BUDGET: readonly string[] = [
  'storage',
  'unlimitedStorage',
  'contextMenus',
  'declarativeNetRequest',
];

/** Permissions that must never appear, even if the budget above is edited carelessly. */
export const FORBIDDEN_PERMISSIONS: readonly string[] = ['tabs', 'scripting', 'activeTab'];

export const REQUIRED_RULESETS: readonly string[] = ['bookwalker_cors'];

export interface ValidationResult {
  ok: boolean;
  files: string[];
  errors: string[];
}

export interface ValidateOptions {
  /** Fonts that must ship with the build. Defaults to the font registry. */
  fonts?: readonly BundledFont[];
  /** DNR ruleset ids that must be declared. Defaults to `REQUIRED_RULESETS`. */
  requiredRulesets?: readonly string[];
  /** Allowed permissions. Defaults to `PERMISSION_BUDGET`. */
  permissionBudget?: readonly string[];
}

interface RuleResource {
  id?: string;
  enabled?: boolean;
  path?: string;
}

interface Manifest {
  manifest_version?: unknown;
  name?: unknown;
  version?: unknown;
  permissions?: string[];
  optional_permissions?: string[];
  host_permissions?: string[];
  icons?: Record<string, string>;
  action?: { default_popup?: string; default_icon?: Record<string, string> | string };
  options_page?: string;
  options_ui?: { page?: string };
  background?: { service_worker?: string };
  content_scripts?: Array<{ js?: string[]; css?: string[] }>;
  web_accessible_resources?: Array<{ resources?: string[] }>;
  declarative_net_request?: { rule_resources?: RuleResource[] };
  content_security_policy?: { extension_pages?: string; sandbox?: string } | string;
}

const REMOTE_URL = /^(?:[a-z][a-z0-9+.-]*:)?\/\//i;

function toPosix(value: string): string {
  return value.replace(/\\/g, '/');
}

function displayPath(filePath: string): string {
  return toPosix(path.relative(process.cwd(), filePath));
}

function readText(filePath: string): string {
  return fs.readFileSync(filePath, 'utf8');
}

function walkFiles(root: string): string[] {
  const results: string[] = [];
  const visit = (directory: string) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(absolute);
      else results.push(toPosix(path.relative(root, absolute)));
    }
  };
  visit(root);
  return results.sort();
}

/**
 * Resolve a reference found in `fromFile` (relative to the extension root).
 * Root-relative references (`/chunks/x.js`, as WXT emits in HTML) resolve
 * against the extension root. Returns null for external/data references.
 */
function resolveReference(fromFile: string, reference: string): string | null {
  const clean = toPosix(reference.trim()).split(/[?#]/)[0] ?? '';
  if (!clean || reference.trim().startsWith('#')) return null;
  if (REMOTE_URL.test(clean) || /^(?:data|blob|mailto|javascript|about):/i.test(clean)) return null;
  const base = clean.startsWith('/')
    ? clean.slice(1)
    : path.posix.join(path.posix.dirname(fromFile), clean);
  return path.posix.normalize(base);
}

class Inventory {
  readonly files = new Set<string>();
  readonly errors: string[] = [];

  /** Add a manifest-declared asset path (rejects remote and escaping paths). */
  addManifestFile(value: unknown): void {
    if (typeof value !== 'string' || !value) return;
    if (REMOTE_URL.test(value)) {
      this.errors.push(`manifest.json must reference a local extension asset: ${value}`);
      return;
    }
    const normalized = path.posix.normalize(toPosix(value).split(/[?#]/)[0] ?? '');
    if (normalized === '..' || normalized.startsWith('../')) {
      this.errors.push(`manifest.json references asset outside extension/: ${value}`);
      return;
    }
    this.files.add(normalized.replace(/^\//, ''));
  }

  addDiscovered(fromFile: string, reference: string): void {
    const resolved = resolveReference(fromFile, reference);
    if (resolved === null) return;
    if (resolved === '..' || resolved.startsWith('../')) {
      this.errors.push(`${fromFile} references asset outside extension/: ${reference}`);
      return;
    }
    this.files.add(resolved);
  }
}

function collectHtmlReferences(inventory: Inventory, root: string, file: string): void {
  const absolute = path.join(root, file);
  if (!fs.existsSync(absolute)) return;
  const html = readText(absolute);
  for (const match of html.matchAll(/\b(?:src|href)=["']([^"']+)["']/gi)) {
    inventory.addDiscovered(file, match[1] ?? '');
  }
}

function collectCssReferences(inventory: Inventory, root: string, file: string): void {
  const absolute = path.join(root, file);
  if (!fs.existsSync(absolute)) return;
  const css = readText(absolute);
  for (const match of css.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/gi)) {
    inventory.addDiscovered(file, (match[1] ?? '').trim());
  }
}

function collectScriptReferences(inventory: Inventory, root: string, file: string): void {
  const absolute = path.join(root, file);
  if (!fs.existsSync(absolute)) return;
  const source = readText(absolute);

  for (const call of source.matchAll(/importScripts\(([^)]*)\)/g)) {
    for (const literal of (call[1] ?? '').matchAll(/["'`]([^"'`]+)["'`]/g)) {
      inventory.addDiscovered(file, literal[1] ?? '');
    }
  }
  // Static and dynamic imports of relative chunks (WXT/Vite splits shared code).
  const importPatterns = [
    /\bfrom\s*["'`](\.{1,2}\/[^"'`]+)["'`]/g,
    /\bimport\s*["'`](\.{1,2}\/[^"'`]+)["'`]/g,
    /\bimport\(\s*["'`](\.{1,2}\/[^"'`]+)["'`]\s*\)/g,
  ];
  for (const pattern of importPatterns) {
    for (const match of source.matchAll(pattern)) {
      inventory.addDiscovered(file, match[1] ?? '');
    }
  }
}

function collectManifestFiles(manifest: Manifest, root: string, inventory: Inventory): void {
  const addIcons = (icons: unknown) => {
    if (typeof icons === 'string') inventory.addManifestFile(icons);
    else if (icons && typeof icons === 'object') {
      for (const icon of Object.values(icons)) inventory.addManifestFile(icon);
    }
  };
  addIcons(manifest.icons);
  addIcons(manifest.action?.default_icon);
  inventory.addManifestFile(manifest.action?.default_popup);
  inventory.addManifestFile(manifest.options_page);
  inventory.addManifestFile(manifest.options_ui?.page);
  inventory.addManifestFile(manifest.background?.service_worker);

  for (const contentScript of manifest.content_scripts ?? []) {
    for (const js of contentScript.js ?? []) inventory.addManifestFile(js);
    for (const css of contentScript.css ?? []) inventory.addManifestFile(css);
  }
  for (const group of manifest.web_accessible_resources ?? []) {
    for (const resource of group.resources ?? []) inventory.addManifestFile(resource);
  }
  for (const ruleResource of manifest.declarative_net_request?.rule_resources ?? []) {
    inventory.addManifestFile(ruleResource.path);
  }

  const visited = new Set<string>();
  const pending = [...inventory.files];
  while (pending.length > 0) {
    const file = pending.shift() as string;
    if (visited.has(file)) continue;
    visited.add(file);
    const before = new Set(inventory.files);
    if (file.endsWith('.html')) collectHtmlReferences(inventory, root, file);
    else if (file.endsWith('.css')) collectCssReferences(inventory, root, file);
    else if (/\.m?js$/.test(file)) collectScriptReferences(inventory, root, file);
    for (const discovered of inventory.files) {
      if (!before.has(discovered)) pending.push(discovered);
    }
  }
}

function checkPermissions(manifest: Manifest, budget: readonly string[], errors: string[]): void {
  const requested = [...(manifest.permissions ?? []), ...(manifest.optional_permissions ?? [])];
  for (const permission of requested) {
    if (FORBIDDEN_PERMISSIONS.includes(permission)) {
      errors.push(`Forbidden permission requested: ${permission}`);
    } else if (!budget.includes(permission)) {
      errors.push(
        `Permission outside the budget (${budget.join(', ')}): ${permission}. ` +
          'Widening the permission budget must be a deliberate change.',
      );
    }
  }
}

function checkFonts(root: string, fonts: readonly BundledFont[], inventory: Inventory): void {
  for (const font of fonts) {
    const fontPath = path.join(root, font.file);
    if (!fs.existsSync(fontPath)) {
      inventory.errors.push(`Bundled font is missing from the build: ${font.file}`);
      continue;
    }
    inventory.addManifestFile(font.file);
    const sha256 = createHash('sha256').update(fs.readFileSync(fontPath)).digest('hex');
    if (sha256 !== font.sha256) {
      inventory.errors.push(`Bundled font does not match its registered checksum: ${font.file}`);
    }
  }
}

function checkRuleResources(
  manifest: Manifest,
  root: string,
  required: readonly string[],
  errors: string[],
): void {
  const resources = manifest.declarative_net_request?.rule_resources ?? [];
  if (manifest.permissions?.includes('declarativeNetRequest') && resources.length === 0) {
    errors.push('manifest.json requests declarativeNetRequest but declares no rule_resources');
  }
  for (const id of required) {
    if (!resources.some((resource) => resource.id === id)) {
      errors.push(`manifest.json is missing the DNR ruleset: ${id}`);
    }
  }
  for (const resource of resources) {
    if (typeof resource.path !== 'string') continue;
    const rulePath = path.join(root, resource.path);
    if (!fs.existsSync(rulePath)) continue; // reported as a missing referenced file
    try {
      const rules: unknown = JSON.parse(readText(rulePath));
      if (!Array.isArray(rules)) errors.push(`DNR ruleset ${resource.path} must be a JSON array`);
    } catch (error) {
      errors.push(`${resource.path} is not valid JSON: ${(error as Error).message}`);
    }
  }
}

/** Directive names whose sources decide what code an extension page may run. */
const CODE_DIRECTIVES = new Set([
  'default-src',
  'script-src',
  'script-src-elem',
  'object-src',
  'worker-src',
]);

function checkCsp(manifest: Manifest, errors: string[]): void {
  const policy = manifest.content_security_policy;
  if (!policy) return;
  const policies = typeof policy === 'string' ? [policy] : Object.values(policy);
  for (const text of policies) {
    for (const directive of text.split(';')) {
      const [name, ...sources] = directive.trim().split(/\s+/);
      if (!name || !CODE_DIRECTIVES.has(name.toLowerCase())) continue;
      for (const source of sources) {
        if (/^'unsafe-(?:eval|inline)'$/i.test(source) || /^(?:https?:|wss?:|\*)/i.test(source)) {
          errors.push(`manifest.json CSP allows non-local code in ${name}: ${source}`);
        }
      }
    }
  }
}

function checkHtmlForRemoteCode(file: string, html: string, errors: string[]): void {
  for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
    const attributes = match[1] ?? '';
    const body = (match[2] ?? '').trim();
    const src = /\bsrc=["']([^"']*)["']/i.exec(attributes)?.[1];
    if (src !== undefined) {
      if (REMOTE_URL.test(src)) errors.push(`${file} loads a remote script: ${src}`);
    } else if (body && !/\btype=["']application\/(?:json|ld\+json)["']/i.test(attributes)) {
      errors.push(`${file} contains an inline script (blocked by the MV3 CSP)`);
    }
  }
  for (const match of html.matchAll(/<link\b([^>]*)>/gi)) {
    const attributes = match[1] ?? '';
    const href = /\bhref=["']([^"']*)["']/i.exec(attributes)?.[1];
    if (
      href &&
      REMOTE_URL.test(href) &&
      /\brel=["'](?:modulepreload|preload|stylesheet)["']/i.test(attributes)
    ) {
      errors.push(`${file} loads a remote resource: ${href}`);
    }
  }
  if (/<[a-z][^>]*\son[a-z]+\s*=/i.test(html)) {
    errors.push(`${file} contains an inline event handler (blocked by the MV3 CSP)`);
  }
}

function checkScriptForRemoteCode(file: string, source: string, errors: string[]): void {
  const remoteLiteral = /["'`]\s*(?:https?:)?\/\//i;
  for (const call of source.matchAll(/importScripts\(([^)]*)\)/g)) {
    if (remoteLiteral.test(call[1] ?? ''))
      errors.push(`${file} calls importScripts with a remote URL`);
  }
  if (/\bimport\(\s*["'`](?:https?:)?\/\//i.test(source)) {
    errors.push(`${file} dynamically imports a remote module`);
  }
  if (/\b(?:from|import)\s*["'`]https?:\/\//i.test(source)) {
    errors.push(`${file} statically imports a remote module`);
  }
}

function checkNoRemoteCode(root: string, manifest: Manifest, errors: string[]): void {
  checkCsp(manifest, errors);
  for (const file of walkFiles(root)) {
    if (file.endsWith('.html'))
      checkHtmlForRemoteCode(file, readText(path.join(root, file)), errors);
    else if (/\.m?js$/.test(file))
      checkScriptForRemoteCode(file, readText(path.join(root, file)), errors);
  }
}

function checkFilesExist(root: string, files: string[], errors: string[]): void {
  const rootReal = fs.realpathSync(root);
  for (const file of files) {
    const absolute = path.join(root, file);
    if (!fs.existsSync(absolute)) {
      errors.push(`Missing extension file referenced by manifest or HTML: ${file}`);
      continue;
    }
    let real: string;
    try {
      real = fs.realpathSync(absolute);
    } catch {
      continue;
    }
    if (real !== rootReal && !real.startsWith(`${rootReal}${path.sep}`)) {
      errors.push(`Referenced extension path escapes extension/ through symlink: ${file}`);
      continue;
    }
    if (!fs.statSync(absolute).isFile()) {
      errors.push(`Referenced extension path is not a file: ${file}`);
    }
  }
}

export function validateExtension(
  extensionDir: string = DIST_DIR,
  options: ValidateOptions = {},
): ValidationResult {
  const fonts = options.fonts ?? BUNDLED_FONTS;
  const requiredRulesets = options.requiredRulesets ?? REQUIRED_RULESETS;
  const budget = options.permissionBudget ?? PERMISSION_BUDGET;

  const manifestPath = path.join(extensionDir, 'manifest.json');
  if (!fs.existsSync(manifestPath)) {
    return {
      ok: false,
      files: [],
      errors: [`Missing manifest: ${displayPath(manifestPath)} (run \`npm run build\` first)`],
    };
  }

  let manifest: Manifest;
  try {
    manifest = JSON.parse(readText(manifestPath)) as Manifest;
  } catch (error) {
    return {
      ok: false,
      files: [],
      errors: [`${displayPath(manifestPath)} is not valid JSON: ${(error as Error).message}`],
    };
  }

  const inventory = new Inventory();
  const errors = inventory.errors;

  if (manifest.manifest_version !== 3) errors.push('manifest.json must declare manifest_version 3');
  if (!manifest.name) errors.push('manifest.json must declare name');
  if (!manifest.version) errors.push('manifest.json must declare version');

  checkPermissions(manifest, budget, errors);
  collectManifestFiles(manifest, extensionDir, inventory);
  checkFonts(extensionDir, fonts, inventory);
  checkRuleResources(manifest, extensionDir, requiredRulesets, errors);
  checkNoRemoteCode(extensionDir, manifest, errors);

  const files = [...inventory.files].sort();
  checkFilesExist(extensionDir, files, errors);

  return { ok: errors.length === 0, files, errors };
}

/** Throw with every problem listed; used by the WXT `build:done` hook. */
export function assertValidExtension(
  extensionDir: string = DIST_DIR,
  options?: ValidateOptions,
): ValidationResult {
  const result = validateExtension(extensionDir, options);
  if (!result.ok) {
    throw new Error(
      `Extension validation failed:\n${result.errors.map((error) => `- ${error}`).join('\n')}`,
    );
  }
  return result;
}

function main(): void {
  const target = process.argv[2] ? path.resolve(process.argv[2]) : DIST_DIR;
  const result = validateExtension(target);
  if (!result.ok) {
    console.error('Extension validation failed:');
    for (const error of result.errors) console.error(`- ${error}`);
    process.exitCode = 1;
    return;
  }
  console.log(`Extension validation passed for ${result.files.length} referenced files.`);
  console.log(`Extension ready at: ${target}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
