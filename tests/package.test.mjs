import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { spawnSync } from "node:child_process";

test("XPI contains its bootstrap, dialog, licenses and no placeholder identities", async () => {
  const manifest = JSON.parse(await fs.readFile(".scaffold/build/manifest.json", "utf8"));
  const pkg = JSON.parse(await fs.readFile("package.json", "utf8"));
  assert.equal(manifest.version, pkg.version);
  assert.equal(manifest.applications.zotero.id, "codex-translate@zotero.local");
  assert.equal(manifest.applications.zotero.strict_min_version, "9.0.0");
  assert.equal(manifest.applications.zotero.strict_max_version, "10.0.*");
  const repository = pkg.repository?.url?.replace(/^git\+/, "").replace(/\.git$/, "").replace("https://github.com/", "");
  assert.equal(manifest.applications.zotero.update_url, repository ? `https://raw.githubusercontent.com/${repository}/main/updates.json` : "https://example.invalid/zotero-codex/updates.json");
  const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
  const wheels = (await fs.readdir(".scaffold/build/content/runtime")).filter(name => name.endsWith(".whl"));
  assert.deepEqual(wheels, [`zotero_codex_bridge-${pkg.version}-py3-none-any.whl`]);
  const result = spawnSync(python, ["-c", `import zipfile,pathlib; z=zipfile.ZipFile('dist/zotero-codex-translate-${pkg.version}.xpi'); assert all(n in z.namelist() for n in ['bootstrap.js','content/translation.xhtml','LICENSE','THIRD_PARTY_NOTICES.md','content/runtime/manifest.json']); assert b'__addon' not in z.read('bootstrap.js'); assert 'content/smoke.js' not in z.namelist(); assert z.getinfo('content/runtime/zotero_codex_bridge-${pkg.version}-py3-none-any.whl').file_size < 1000000; assert z.read('content/runtime/manifest.json') == pathlib.Path('.scaffold/build/content/runtime/manifest.json').read_bytes(); assert z.read('content/scripts/zotero-codex-translate.js') == pathlib.Path('.scaffold/build/content/scripts/zotero-codex-translate.js').read_bytes()`], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
});

test("UI only uses DOM text for document-derived content", async () => {
  const ui = await fs.readFile("addon/content/translation-ui.js", "utf8");
  assert.doesNotMatch(ui, /innerHTML|insertAdjacentHTML|eval\(/);
  assert.match(ui, /textContent/);
});
