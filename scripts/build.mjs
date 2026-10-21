import { build } from "esbuild";
import fs from "node:fs/promises";
import path from "node:path";
import { execFileSync } from "node:child_process";

const root = process.cwd();
const pkg = JSON.parse(await fs.readFile("package.json", "utf8"));
const output = path.join(root, ".scaffold", "build");
await fs.mkdir(output, { recursive: true });
const definitions = { ...pkg.config, description: pkg.description, author: pkg.author, homepage: pkg.homepage || "", buildVersion: pkg.version, buildTime: new Date().toISOString() };
async function copyDirectory(source, destination) {
  for (const entry of await fs.readdir(source, { withFileTypes: true })) {
    let name = entry.name;
    if (name.endsWith(".ftl")) name = `${pkg.config.addonRef}-${name}`;
    const from = path.join(source, entry.name);
    const to = path.join(destination, name);
    if (entry.isDirectory()) {
      await fs.mkdir(to, { recursive: true });
      await copyDirectory(from, to);
    } else {
      let data = await fs.readFile(from);
      if (/\.(json|js|xhtml|css|ftl)$/.test(name)) {
        let text = data.toString("utf8");
        text = text.replace(/__(\w+)__/g, (match, key) => definitions[key] ?? match);
        if (name.endsWith(".ftl")) text = text.replace(/^([a-zA-Z][\w-]*)(\s*=)/gm, `${pkg.config.addonRef}-$1$2`);
        if (name.endsWith(".xhtml")) text = text.replace(/data-l10n-id="(?!zotero-codex-translate-)([^"]+)"/g, `data-l10n-id="${pkg.config.addonRef}-$1"`);
        if (name.endsWith(".xhtml")) text = text.replace(/preference="([^"]+)"/g, `preference="${pkg.config.prefsPrefix}.$1"`);
        if (name === "prefs.js") text = text.replace(/pref\("([^"]+)"/g, `pref("${pkg.config.prefsPrefix}.$1"`);
        data = Buffer.from(text);
      }
      await fs.mkdir(path.dirname(to), { recursive: true });
      await fs.writeFile(to, data);
    }
  }
}
await copyDirectory("addon", output);
for (const name of ["LICENSE", "THIRD_PARTY_NOTICES.md", "UPSTREAM.json"]) await fs.copyFile(name, path.join(output, name));
execFileSync(process.env.PYTHON || (process.platform === "win32" ? "python" : "python3"), ["scripts/build_runtime.py"], { stdio: "inherit" });
const bundleResult = await build({ entryPoints: ["src/index.ts"], bundle: true, metafile: true, target: "firefox140", format: "iife",
  define: { __env__: '"production"' }, outfile: path.join(output, "content", "scripts", `${pkg.config.addonRef}.js`) });
await fs.mkdir("tmp", {recursive: true});
await fs.writeFile("tmp/bundle-inputs.json", JSON.stringify(bundleResult.metafile.inputs, null, 2));
execFileSync(process.execPath, ["node_modules/typescript/bin/tsc", "--noEmit"], { stdio: "inherit" });
execFileSync(process.env.PYTHON || (process.platform === "win32" ? "python" : "python3"), ["scripts/build_xpi.py"], { stdio: "inherit" });
