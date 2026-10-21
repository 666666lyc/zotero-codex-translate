# Linux x64 / Zotero 10 local adaptation

This local build retains Zotero 9 support and admits Zotero 10. Linux uses a
separate Python 3.12 virtual environment and a hash-locked set of official PyPI
wheels. It does not run the Windows bootstrap or modify system Python.

1. Build with Node.js and Python 3.12: `npm ci --ignore-scripts && npm run build`.
2. Close Zotero. Run `python3.12 scripts/install_linux.py --data-dir /absolute/path/to/Zotero/codex-translate`.
   Use the **data directory**, not the profile directory. First preparation downloads
   dependencies and the upstream PDF assets and needs several GB of disk space.
3. Install the XPI from `dist/` through Zotero's plugin manager; restart Zotero.
4. Under Tools → Codex translation connection settings, set the full path to a
   native Codex executable if the desktop session's PATH does not include it.

On startup Linux checks its dedicated pointer, installed bridge version, native
PDF imports and every required resource hash. Missing or relocated environments
produce an actionable error; rerun the installer after a source upgrade.
The Windows managed runtime remains separate and unchanged.

Test translations send only the synthetic fixture to the existing Codex account.
This adaptation is a local development build, not an upstream supported release.


## Version 0.1.1: pairing and sync metadata

The comparison button now accepts an existing sibling PDF: one unassociated
candidate opens directly; multiple candidates use a selection dialog. Existing
saved pairings take precedence. Missing or moved sources do not silently remap.

Pair metadata is stored as a versioned `ZOTERO_CODEX_TRANSLATION_V1:` record in
an independent paragraph at the end of the translated attachment's note. It
contains the original item key and optional fingerprint, job ID and page counts.
User note content is retained. This record uses the normal syncable `note` field;
no task directory or local numeric item ID is required on the receiving device.
Do not edit the plugin record manually. Corrupt or conflicting records are reported
and preserved rather than overwritten. Read-only libraries permit temporary pairs.

Startup migrates recognized legacy translation/page-count URNs out of
`dc:relation` in writable libraries. Original notes and relations are backed up
under `codex-translate/migration-backups/` before changes. Each attachment's
migration is transactional and repeatable. Other relations remain intact.

Page counts are read from the actual readers. Equal page counts start with sync
on; unequal or unconfirmed counts start independently. Unequal counts display a
warning; synchronized page numbers do not imply corresponding document content.

Both devices need the updated plugin. Library metadata and attachment file sync
must complete separately. Local two-profile JSON transport and restart tests pass;
real cloud cross-device sync has not been exercised by this version.
No new translation or model request is needed to open existing PDFs side by side.
