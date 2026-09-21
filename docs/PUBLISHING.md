# Publishing a version

1. Set `__version__` in `source/metadata_transfer/__init__.py` and add a section to `CHANGELOG.md`.
2. Build and check:
   ```
   packaging\build.ps1
   packaging\test-installer.ps1 -Setup "Metadata Transfer Setup <x.y>.exe" -Uninstall
   ```
3. Release files (fixed names, zip, checksums):
   ```
   python tools/release_assets.py <folder>
   ```
4. Repository folder (privacy scan included; list sample and project names that must not appear in
   `METADATA_TRANSFER_SCRUB_WORDS`, or `@<file>` with one word per line):
   ```
   python tools/export_public_repo.py --replace
   ```
   The first time, create the GitHub repository from it (private until you decide otherwise):
   ```
   gh repo create AlanRajendran/metadata-transfer --private --source "<repo folder>" --remote origin --push
   ```
   Later versions: the export keeps the repository's history and adds one commit; then `git push`.
5. Release:
   ```
   gh release create v<x.y.z> <folder>\* --title "Metadata Transfer <x.y.z>" --notes-file docs/CHANGELOG.md
   ```
   The README's download links (`releases/latest/download/...`) then point to the new files, and
   Help → Check for updates finds the version (for a private repository only for signed-in collaborators).
