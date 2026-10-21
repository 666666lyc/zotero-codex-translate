"""Set the authenticated, verified project repository before public release."""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("repository", help="Verified owner/zotero-codex-translate")
args = parser.parse_args()
if not re.fullmatch(r"[A-Za-z0-9-]+/zotero-codex-translate", args.repository):
    parser.error("Expected owner/zotero-codex-translate")
package = ROOT / "package.json"
value = json.loads(package.read_text(encoding="utf-8"))
url = "https://github.com/" + args.repository
value.update(homepage=url + f"/tree/v{value['version']}", repository={"type": "git", "url": url + ".git"})
package.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
manifest = ROOT / "addon/manifest.json"
value = json.loads(manifest.read_text(encoding="utf-8"))
value["applications"]["zotero"]["update_url"] = "https://raw.githubusercontent.com/" + args.repository + "/main/updates.json"
manifest.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
# Zotero 9 requires an update URL. Keep the valid manifest empty: upgrades are
# manually installed XPI files; no update is advertised through this endpoint.
(ROOT / "updates.json").write_text(json.dumps({"addons": {"codex-translate@zotero.local": {"updates": []}}}, indent=2) + "\n", encoding="utf-8")
print(url)
