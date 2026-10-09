# winget manifest schemas

The JSON schemas `tools/winget_manifest.py` checks the manifests it writes
against: manifest version 1.12.0, from the Windows Package Manager's own
repository, unchanged.

- Source: <https://github.com/microsoft/winget-cli/tree/efcb928ba96d314bc8aee8682a1af3588bb8e3f1/schemas/JSON/manifests/v1.12.0>
  (the last commit to change them, 2025-12-23)
- Files: `manifest.version.1.12.0.json`, `manifest.installer.1.12.0.json`,
  `manifest.defaultLocale.1.12.0.json`
- Licence: MIT, Copyright (c) Microsoft Corporation; see `LICENSE` here,
  winget-cli's own licence file

They are build tooling, not part of the app, so they aren't in the app's
third-party notices. To move to a newer manifest version, replace the three
files with that version's, change `MANIFEST_VERSION` in
`tools/winget_manifest.py`, and run its tests: the checker refuses a schema
that uses a keyword it doesn't apply.
