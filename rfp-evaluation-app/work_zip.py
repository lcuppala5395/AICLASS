"""Package the project into a clean zip for submission or upload.

Usage:  python work_zip.py [output.zip]
Default output: ../<project-folder>_submission.zip (outside the project).
Excluded: virtual envs, caches, git data, *.db files and ANY secrets (.env, secrets.toml).
"""
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKIP_DIRS = {"__pycache__", ".venv", "venv", ".git", ".pytest_cache", ".idea", ".vscode"}
SKIP_NAMES = {".env", "secrets.toml"}
SKIP_SUFFIXES = {".db", ".sqlite3", ".pyc", ".zip"}


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / f"{ROOT.name}_submission.zip"
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(ROOT.rglob("*")):
            rel = path.relative_to(ROOT)
            if (not path.is_file() or any(part in SKIP_DIRS for part in rel.parts)
                    or path.name in SKIP_NAMES or path.suffix in SKIP_SUFFIXES):
                continue
            zf.write(path, Path(ROOT.name) / rel)
            count += 1
    print(f"Wrote {out} ({count} files). Secrets and database files were left out.")


if __name__ == "__main__":
    main()
