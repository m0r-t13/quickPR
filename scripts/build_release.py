"""Build a small, source-only archive that works on both target platforms."""

from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def main():
    root = Path(__file__).resolve().parents[1]
    destination = root / "dist" / "quickPR-0.2.0.zip"
    destination.parent.mkdir(exist_ok=True)
    files = [root / name for name in ("README.md", "pyproject.toml", ".gitignore", "start-macos.command", "start-windows.cmd")]
    for folder in ("quickpr", "tests", "docs", ".github", "scripts"):
        files.extend(path for path in (root / folder).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, "quickPR/" + path.relative_to(root).as_posix())
    print("{} ({} bytes)".format(destination, destination.stat().st_size))


if __name__ == "__main__":
    main()
