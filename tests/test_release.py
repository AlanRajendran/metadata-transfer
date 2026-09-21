# SPDX-License-Identifier: GPL-3.0-or-later
"""What a release needs: credit and disclaimer in the app, no machine paths or addresses in the
published files, release files with fixed names and matching checksums, a clean repository export."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP = Path(__file__).resolve().parent.parent


def tool(name: str):
    spec = importlib.util.spec_from_file_location(name, APP / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_about_box_shows_credit_disclaimer_and_licence():
    from PySide6.QtWidgets import QApplication

    from metadata_transfer import CREDITS, DISCLAIMER, LICENSE_NAME, RESEARCH_USE
    from metadata_transfer.gui.dialogs import AboutDialog

    _app = QApplication.instance() or QApplication([])
    text = AboutDialog().text()
    for part in (CREDITS, DISCLAIMER, RESEARCH_USE, LICENSE_NAME, "BIOMIS Team"):
        assert part in text


def test_crash_report_hides_the_user_name(monkeypatch, tmp_path):
    from metadata_transfer.gui.dialogs import build_report

    home = tmp_path / "Users" / "someone"
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    victim = str(home / "data.nd2")
    try:
        raise ValueError(f"cannot read {victim}")
    except ValueError as exc:
        report = build_report(type(exc), exc, exc.__traceback__)
    assert "someone" not in report and "%USERPROFILE%" in report


def test_scrub_check_finds_paths_addresses_and_words(tmp_path):
    profile_path = "C:" + "\\Users\\" + "anyone\\data"  # built from pieces, so this file stays clean
    address = "anyone" + "@" + "lab.org"
    (tmp_path / "notes.txt").write_text(f"saved to {profile_path} by {address} for projectx\n", encoding="utf-8")
    (tmp_path / "ok.txt").write_text("%APPDATA%\\Metadata Transfer and C:\\Users\\<you>\n", encoding="utf-8")
    findings = tool("scrub_check").scan(tmp_path, extra_words=["projectx"])
    assert len(findings) == 3 and all(f.startswith("notes.txt") for f in findings)


def test_export_makes_a_clean_repository_folder(tmp_path):
    target = tool("export_public_repo").export(tmp_path / "repo", init_git=False)
    for rel in ("README.md", "LICENSE", ".gitignore", ".github/workflows/tests.yml",
                ".github/ISSUE_TEMPLATE/bug_report.md", "source/metadata_transfer/__init__.py",
                "source/metadata_transfer/formats/data/vsi_template.vsi", "source/metadata_transfer/assets/icon.ico",
                "docs/images/main_window.png", "packaging/installer.nsi"):
        assert (target / rel).is_file(), rel
    assert not list(target.rglob("*.exe")) and not list(target.rglob("__pycache__"))
    assert not (target / "packaging" / "THIRD_PARTY_NOTICES.txt").exists()
    assert "GNU GENERAL PUBLIC LICENSE" in (target / "LICENSE").read_text(encoding="utf-8")


def test_release_files_zip_and_checksums(tmp_path):
    module = tool("release_assets")
    root = tmp_path / "version"
    (root / "packaging").mkdir(parents=True)
    (root / "packaging" / "THIRD_PARTY_NOTICES.txt").write_text("notices", encoding="utf-8")
    (root / "LICENSE").write_text("GPL", encoding="utf-8")
    for name in module.release_names():
        (root / name).write_bytes(name.encode("utf-8"))
    module.make_release_assets(root, tmp_path / "out")
    out = tmp_path / "out"
    assert sorted(p.name for p in out.iterdir()) == [
        "Metadata-Transfer-Setup.exe", "Metadata-Transfer-Windows.zip", "SHA256SUMS.txt", "Uninstall-Metadata-Transfer.exe"]
    for line in (out / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ")
        assert " " not in name and hashlib.sha256((out / name).read_bytes()).hexdigest() == digest
    with zipfile.ZipFile(out / "Metadata-Transfer-Windows.zip") as zf:
        names = sorted(n.split("/", 1)[1] for n in zf.namelist())
        assert names == ["LICENSE.txt", "Metadata-Transfer-Setup.exe", "README.txt", "SHA256SUMS.txt",
                         "THIRD_PARTY_NOTICES.txt", "Uninstall-Metadata-Transfer.exe"]


def test_vsi_template_has_no_personal_values():
    from metadata_transfer.formats.vsi_document import load_template

    root = load_template()
    texts = [f.get_str() for _v, f in root.walk() if f.payload and f.data_type in (13, 8192)]
    doc = root.sub(2000).sub(2004).sub(2109)
    assert doc.need(15).get_str() == ""  # author
    assert root.sub(2000).sub(2001, 1).sub(2005).sub(21000).need(175266).get_str() == ""  # experiment
    assert not [t for t in texts if ":\\" in t or "/Users/" in t]
