"""Build separate HA/app archives and one complete offline bundle."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent
COMPONENT = ROOT / "custom_components" / "china_carriers"
APP = ROOT / "china_mobile_browser"
RELEASES = ROOT / "releases"


def source_files(folder: Path) -> list[Path]:
    return [
        path for path in sorted(folder.rglob("*"))
        if path.is_file() and path.suffix not in {".pyc", ".pyo"}
        and "__pycache__" not in path.parts
    ]


def archive_files(destination: Path, files: list[Path]) -> None:
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    print(destination)


def main() -> None:
    RELEASES.mkdir(exist_ok=True)
    component_files = source_files(COMPONENT)
    app_files = source_files(APP)
    documents = [ROOT / "README.md", ROOT / "INSTALL.md", ROOT / "LICENSE"]
    archive_files(RELEASES / "china_carriers-ha.zip", component_files + documents)
    archive_files(RELEASES / "china_mobile_10086-haos-app.zip", app_files)
    archive_files(
        RELEASES / "china_carriers-complete.zip",
        component_files + app_files + documents,
    )


if __name__ == "__main__":
    main()
