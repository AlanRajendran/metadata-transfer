# SPDX-License-Identifier: GPL-3.0-or-later AND MIT
"""Build the GitHub repository folder from this version folder. Nothing is pushed.

    python tools/export_public_repo.py [target] [--replace] [--no-git]

The target (default: "repo/metadata-transfer" beside this version folder) receives the source,
tests, packaging, tools and docs of this folder, without built programs, generated files and
caches, plus LICENSE, a .gitignore and the .github folder (workflow and issue template from
packaging/github). The privacy scan (tools/scrub_check.py, with the words listed in
METADATA_TRANSFER_SCRUB_WORDS) then runs on the result; if it is clean, a git repository with one
commit is made (author: the maintainer's GitHub no-reply identity). Publishing: docs/PUBLISHING.md.
(Pattern of the BIOMIS team's Timelapse Video Processing.)
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
AUTHOR = ("AlanRajendran", "28244861+AlanRajendran@users.noreply.github.com")
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".pytest_tmp", "dist", "build"}
SKIP_SUFFIXES = {".exe", ".sha256", ".part", ".pyc"}
#: generated at build time (they hold build-machine details) or local-only
SKIP_FILES = {"packaging/THIRD_PARTY_NOTICES.txt", "packaging/version_info.txt", "samples.json"}
INCLUDE_TOP = ("README.md", "LICENSE", "pytest.ini", ".gitattributes", "source", "tests", "packaging", "tools", "docs")


def _writable_then_retry(function, path, _exc) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _version() -> str:
    sys.path.insert(0, str(APP / "source"))
    from metadata_transfer import VERSION

    return VERSION


def source_files(app: Path = APP) -> list[Path]:
    out = []
    for top in INCLUDE_TOP:
        p = app / top
        if p.is_file():
            out.append(Path(top))
            continue
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for f in filenames:
                rel = (Path(dirpath) / f).relative_to(app)
                if rel.suffix.lower() in SKIP_SUFFIXES or rel.as_posix() in SKIP_FILES:
                    continue
                out.append(rel)
    return sorted(out)


def _scrub(folder: Path) -> list[str]:
    spec = importlib.util.spec_from_file_location("scrub_check", APP / "tools" / "scrub_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.scan(folder, extra_words=module.words_from_env())


def export(target: Path, replace: bool = False, init_git: bool = True) -> Path:
    target = Path(target).resolve()
    if target.exists() and any(p.name != ".git" for p in target.iterdir()):
        if not replace:
            raise SystemExit(f"{target} is not empty; use --replace to rebuild it")
        if not (target / "source" / "metadata_transfer" / "__init__.py").is_file():
            raise SystemExit(f"{target} does not look like an earlier export; not deleting it")
        for child in target.iterdir():
            if child.name == ".git":
                continue  # keep the history: the new commit goes on top
            if child.is_dir():
                shutil.rmtree(child, onexc=_writable_then_retry)
            else:
                child.unlink()
    target.mkdir(parents=True, exist_ok=True)
    for rel in source_files():
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(APP / rel, destination)
    templates = APP / "packaging" / "github"
    shutil.copy2(templates / "gitignore", target / ".gitignore")
    shutil.copytree(templates / "workflows", target / ".github" / "workflows", dirs_exist_ok=True)
    shutil.copytree(templates / "ISSUE_TEMPLATE", target / ".github" / "ISSUE_TEMPLATE", dirs_exist_ok=True)

    findings = _scrub(target)
    if findings:
        print("\n".join(findings))
        raise SystemExit(f"The privacy scan found {len(findings)} problem(s) in {target}; nothing was committed.")
    if init_git:
        name, email = AUTHOR
        git = ["git", "-C", str(target), "-c", f"user.name={name}", "-c", f"user.email={email}"]
        if not (target / ".git").exists():
            subprocess.run(["git", "init", "-q", "-b", "main", str(target)], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", f"Metadata Transfer {_version()}"], check=True)
    return target


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    target = Path(args[0]) if args else APP.parent / "repo" / "metadata-transfer"
    folder = export(target, replace="--replace" in argv, init_git="--no-git" not in argv)
    count = sum(1 for p in folder.rglob("*") if p.is_file() and ".git" not in p.relative_to(folder).parts)
    print(f"Repository folder ready: {folder} ({count} files, privacy scan clean).")
    print("Next (docs/PUBLISHING.md): review it, then")
    print(f'  gh repo create {AUTHOR[0]}/metadata-transfer --private --source "{folder}" --remote origin --push')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
