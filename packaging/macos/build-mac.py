#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Builds ALumenans for the Mac. Runs on Windows.

We don't have a Mac, but we need to build for one. Turns out we don't
need one: a macOS application is just a folder with a particular
structure, and zip can store file permissions even when built on Windows.

Python is bundled inside, the same way it is embedded in the Windows
installer. This is the key decision in this file. Without it, people
would have to install Python themselves, and the one they already have
may not fit: onnxruntime and torch have no builds for Intel macOS.
Forcing someone to reinstall a working Python because of our choice of
models is wrong; it's simpler to bring our own.

Python comes from python-build-standalone: ordinary official CPython
builds, portable, no installation needed. It is downloaded once and
kept alongside, in runtime-cache, so the next build is instant.

The icon. The .icns format is a container with PNGs inside; it is
assembled right here, without Apple's iconutil.

Signing. On Apple Silicon the system won't run unsigned programs, but
that rule is about machine code, and our launcher is a plain shell
script executed by the system bash. There is nothing to sign.

What's left for the person with the Mac: allow it to run once in System
Settings. Nothing else.

Usage:
    python "packaging\\macos\\build-mac.py"
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME_CACHE = HERE / "runtime-cache"          # downloaded Python, so we don't download it twice
PYTHON_VERSION = "3.13"                        # version inside the application
REPO_ROOT = HERE.parents[1]
ISS = REPO_ROOT / "packaging" / "windows" / "ALumenans.iss"
PACKAGE = REPO_ROOT / "src" / "alumenans"
PACKAGE_RESOURCES = PACKAGE / "resources"
PYPROJECT = REPO_ROOT / "pyproject.toml"
# The oldest macOS that all the wheels exist for: torch 2.13 is built only
# for macosx_14_0_arm64. The same number is in Info.plist (LSMinimumSystemVersion).
MACOS_MIN = "14.0"
PLATFORM = "aarch64-apple-darwin"

sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "packaging"))
from alumenans.dependencies import required_modules  # noqa: E402
import standalone_python  # noqa: E402
import third_party_licenses  # noqa: E402

# Permissions inside a zip are stored in the upper two bytes of
# external_attr. Without them the launcher arrives on the Mac without the
# "executable" flag, and the application silently fails to open — the most
# galling breakage there could be.
REGULAR = 0o100644 << 16
EXECUTABLE = 0o100755 << 16
DIRECTORY = (0o040755 << 16) | 0x10


def version() -> str:
    found = re.search(
        r'#define AppVersion "([^"]+)"', ISS.read_text(encoding="utf-8-sig")
    )
    if not found:
        raise SystemExit("No version found in ALumenans.iss.")
    return found.group(1)


def program_files() -> list[str]:
    """The whole alumenans package, the same way the Windows installer takes it.

    Files used to be listed one by one, and the two lists inevitably
    drifted apart. Now both builds take the package whole; there is
    nothing in it to miss."""

    if not (PACKAGE / "__main__.py").is_file():
        raise SystemExit(f"Missing files: {PACKAGE / '__main__.py'} — the package is not in place.")
    names = sorted(
        path.relative_to(PACKAGE).as_posix()
        for path in PACKAGE.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    )
    if not names:
        raise SystemExit("No files found in src/alumenans.")
    return names


# -- icon -----------------------------------------------------------------
#
# .icns: the "icns" header, the length of the whole file, then chunks: four
# bytes of name, four bytes of length, the content. We put ready-made PNGs
# inside.

SIZES = {
    "icp4": 16, "icp5": 32, "icp6": 64,
    "ic07": 128, "ic08": 256, "ic09": 512, "ic10": 1024,
}


PREBUILT_ICON = HERE / "ALumenans.icns"


def icon(source: Path) -> bytes:
    """The icon sits alongside, ready-made: assembled in advance, once.

    That way the build machine doesn't need Pillow. If there's no ready one,
    we'll assemble it, but then Pillow will be needed."""

    if PREBUILT_ICON.is_file():
        return PREBUILT_ICON.read_bytes()

    from PIL import Image

    image = Image.open(source).convert("RGBA")
    pieces = []
    for tag, size in SIZES.items():
        buffer = io.BytesIO()
        image.resize((size, size), Image.LANCZOS).save(buffer, format="PNG")
        data = buffer.getvalue()
        pieces.append(tag.encode("ascii") + struct.pack(">I", len(data) + 8) + data)
    body = b"".join(pieces)
    return b"icns" + struct.pack(">I", len(body) + 8) + body


# -- Python into the application ------------------------------------------


def find_python_build() -> tuple[str, str]:
    """Asks GitHub where the portable CPython for the Mac is.

    The search by attributes is in packaging/standalone_python.py, shared
    with the Windows build."""

    try:
        release = standalone_python.latest_release()
    except Exception as error:
        raise SystemExit(
            f"Could not ask GitHub where to get Python ({error}).\n\n"
            "You can do it by hand, once:\n"
            "  1. Open github.com/astral-sh/python-build-standalone/releases\n"
            f"  2. In the latest release, find the file with cpython-{PYTHON_VERSION}, "
            "aarch64-apple-darwin and install_only in its name\n"
            f"  3. Download it into {RUNTIME_CACHE}\n"
            "  4. Run the build again"
        ) from None
    try:
        return standalone_python.pick_asset(release, PYTHON_VERSION, "aarch64-apple-darwin")
    except LookupError:
        raise SystemExit(
            f"Release {release.get('tag_name')} has no CPython {PYTHON_VERSION} build for the Mac."
        ) from None


def python_for_mac() -> Path:
    """Returns the folder with the unpacked Python. Downloads it once."""

    ready = RUNTIME_CACHE / "python"
    if (ready / "bin" / "python3").exists():
        return ready

    # A manually downloaded one will do too — we take whatever is there.
    RUNTIME_CACHE.mkdir(parents=True, exist_ok=True)
    found_locally = sorted(
        path
        for path in RUNTIME_CACHE.glob(f"cpython-{PYTHON_VERSION}*aarch64-apple-darwin*install_only*.tar.gz")
        if "freethreaded" not in path.name
    )
    if found_locally:
        archive = found_locally[-1]
        print(f"Using the already downloaded {archive.name}")
    else:
        name, url = find_python_build()
        archive = RUNTIME_CACHE / name
    if not archive.is_file():
        print(f"Downloading Python for the Mac: {name}")
        print("This happens once; later builds are instant.")
        standalone_python.download(url, archive)

    print("Unpacking…")
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(RUNTIME_CACHE)
    if not (ready / "bin" / "python3").exists():
        raise SystemExit("The downloaded build has no bin/python3 — the format has changed.")
    # Check on the spot: in the build without the GIL the folder is called
    # python3.13t. Catching it here is cheaper than on someone else's Mac.
    check_runtime(ready)
    if list((ready / "lib").glob("python3.*t")):
        raise SystemExit(
            "Got a free-threaded build — regular wheels don't fit it."
            f"\nDelete the contents of {RUNTIME_CACHE} and run the build again."
        )
    return ready


def is_executable(path: Path, root: Path) -> bool:
    """Whether the file must be executable on the Mac.

    We can't ask the file system for permissions: on Windows the
    executable bit doesn't exist, ``st_mode`` there never has it, and
    the whole runtime ships as ordinary files. On the Mac, after that,
    the app says "there's no Python inside" — even though Python is inside.

    So we decide by what the file is: everything in bin/, shared
    libraries, files starting with "#!", and Mac machine code with no
    extension — for example, ffmpeg inside imageio_ffmpeg."""

    relative = path.relative_to(root)
    if relative.parts and relative.parts[0] == "bin":
        return True
    if path.suffix in {".so", ".dylib"}:
        return True
    if path.name.endswith(".so") or ".so." in path.name:
        return True
    try:
        start = path.open("rb").read(4)
    except OSError:
        return False
    return start[:2] == b"#!" or start in MACH_O


# The first four bytes of Mac machine code: 32 and 64 bits in both byte
# orders, and a "fat" file for several processor architectures.
MACH_O = {
    b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
}


# -- packages -------------------------------------------------------------


def packages_for_mac() -> Path:
    """Installs the dependencies from pyproject.toml into a separate folder — for the Mac, from here.

    The program no longer fetches packages on first launch: everything
    ships inside the app. We don't have a Mac, so we cross-install.
    uv does it, not pip: pip with --platform swaps only the wheel tags,
    but evaluates markers like `sys_platform == "darwin"` against the
    build machine — and pyobjc, without which there's no window on the
    Mac, would silently go missing.

    The result goes into RUNTIME_CACHE and is reused until the
    dependencies in pyproject.toml change."""

    import tomllib

    with PYPROJECT.open("rb") as file:
        dependencies = tomllib.load(file)["project"]["dependencies"]
    fingerprint = hashlib.sha256(
        json.dumps([PLATFORM, MACOS_MIN, PYTHON_VERSION, dependencies]).encode("utf-8")
    ).hexdigest()[:12]
    ready = RUNTIME_CACHE / f"packages-{fingerprint}"
    if (ready / ".done").is_file():
        print(f"Using the already installed packages: {ready.name}")
        return ready

    uv = shutil.which("uv")
    if not uv:
        raise SystemExit(
            "uv is required (https://docs.astral.sh/uv/): it installs the Mac"
            " packages right here, without a Mac."
        )
    if ready.exists():
        shutil.rmtree(ready)
    print("Installing packages for the Mac. This happens once per set of dependencies.")
    environment = dict(os.environ, MACOSX_DEPLOYMENT_TARGET=MACOS_MIN)
    answer = subprocess.run(
        [
            uv, "pip", "install",
            "--target", str(ready),
            "--python-platform", PLATFORM,
            "--python-version", PYTHON_VERSION,
            "-r", str(PYPROJECT),
        ],
        env=environment,
    )
    if answer.returncode:
        raise SystemExit(f"uv could not install the Mac packages (exit code {answer.returncode}).")
    check_packages(ready)
    (ready / ".done").write_text(fingerprint, encoding="utf-8")
    return ready


def check_packages(root: Path) -> None:
    """Whether everything needed arrived and there's no foreign machine code.

    A package that has only sources gets built by uv on the build
    machine. For pure Python that's harmless, but a C extension would be
    built for Windows and wouldn't load on the Mac. We catch it here."""

    missing = [
        module
        for module in required_modules("darwin")
        if not (root / module).is_dir()
        and not (root / f"{module}.py").is_file()
        and not list(root.glob(f"{module}*.so"))
    ]
    if missing:
        raise SystemExit("Missing from the Mac packages: " + ", ".join(missing))

    # The main sign is the wheel tag: an extension built here from sources
    # is tagged win_amd64 or linux, not macosx. .exe and .dll files mean
    # nothing by themselves: they're put into generic wheels as data
    # (setuptools launchers, pywebview's WebView2), and on the Mac they're
    # simply not used.
    foreign = []
    for tag in root.glob("*.dist-info/WHEEL"):
        for line in tag.read_text(encoding="utf-8").splitlines():
            if line.startswith("Tag:") and any(
                foreign_tag in line for foreign_tag in ("win", "linux")
            ):
                foreign.append(tag.parent)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".pyd":
            foreign.append(path)
        elif path.suffix in {".so", ".dylib"}:
            with path.open("rb") as file:
                if file.read(4) not in MACH_O:
                    foreign.append(path)
    if foreign:
        raise SystemExit(
            "The Mac packages contain machine code that isn't for the Mac:\n  "
            + "\n  ".join(str(p.relative_to(root)) for p in foreign[:10])
            + "\nSo some package was built from source here."
        )


def check_runtime(root: Path) -> None:
    """Whether the Python that ships to the person has everything.

    Checked here, not on someone else's Mac: there, any missing piece looks
    the same — "couldn't prepare the program" midway through
    installation, after half a gigabyte has already been downloaded."""

    stdlib_dir = root / "lib" / f"python{PYTHON_VERSION}"
    needed = {
        "pip": (root / "lib" / f"python{PYTHON_VERSION}" / "site-packages" / "pip").is_dir(),
        "ensurepip": (stdlib_dir / "ensurepip").is_dir(),
        "tkinter": (stdlib_dir / "tkinter" / "__init__.py").is_file(),
        "_tkinter": bool(list((stdlib_dir / "lib-dynload").glob("_tkinter*.so"))),
        "tcl/tk": bool(list((root / "lib").glob("tk*"))),
    }
    # In these builds ssl, sqlite3, lzma, bz2 are baked into the binary
    # itself; they can't be found as separate modules — we search for their
    # names inside it.
    binary = root / "bin" / f"python{PYTHON_VERSION}"
    if binary.is_file():
        contents = binary.read_bytes()
        for name in ("_ssl", "_sqlite3", "_lzma", "_bz2", "zlib"):
            needed[name] = name.encode("ascii") in contents

    missing = sorted(name for name, present in needed.items() if not present)
    if missing:
        raise SystemExit(
            "The downloaded Python is missing: " + ", ".join(missing)
            + "\nWithout these, the program can't set itself up on the Mac."
        )


def put_tree(
    zip_, inside: str, put, root: Path, prefix: str = "",
    skip: frozenset[tuple[str, ...]] = frozenset(),
) -> int:
    """Puts the runtime into the app, preserving executable permissions.

    Permissions matter most: without the "executable" flag on bin/python3
    the app silently won't start."""

    count, executables = 0, 0
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if parts == (".done",) or any(parts[: len(p)] == p for p in skip):
            continue
        inner_path = f"{inside}/Contents/Resources/python/{prefix}{path.relative_to(root).as_posix()}"
        if path.is_dir():
            put(inner_path + "/", b"", DIRECTORY)
            continue
        if path.is_symlink():
            # Links inside the runtime point to neighbouring files; we store
            # them as links, otherwise the size doubles.
            entry = zipfile.ZipInfo(inner_path, date_time=(2026, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (0o120777 << 16)
            zip_.writestr(entry, str(path.readlink()))
            count += 1
            continue
        allowed = is_executable(path, root)
        put(inner_path, path.read_bytes(), EXECUTABLE if allowed else REGULAR)
        count += 1
        executables += allowed
    if not executables:
        raise SystemExit("Not a single runtime file is marked executable — that can't be right.")
    return count


def check_permissions(zip_, inside: str) -> None:
    """Makes sure there will be something to run.

    A permissions bug shows up only on the Mac and only as "there's no
    Python inside". We catch it here, while the archive is still in hand."""

    required = (
        f"{inside}/Contents/MacOS/ALumenans",
        f"{inside}/Contents/Resources/python/bin/python3",
    )
    entries = {info.filename: info for info in zip_.infolist()}
    for name in required:
        entry = entries.get(name)
        if entry is None:
            raise SystemExit(f"The archive has no {name}.")
        unix_mode = entry.external_attr >> 16
        is_link = (unix_mode & 0o170000) == 0o120000
        if not is_link and not unix_mode & 0o111:
            raise SystemExit(
                f"{name} was packed without the execute permission ({oct(unix_mode)}).\n"
                "On the Mac this looks like \"there's no Python inside the app\"."
            )


# -- build ----------------------------------------------------------------


def assemble() -> Path:
    number = version()
    names = program_files()
    plist = (HERE / "Info.plist").read_text(encoding="utf-8").replace("VERSION", number)
    launcher_script = (HERE / "launcher").read_text(encoding="utf-8")
    # The Mac doesn't understand Windows line endings in a shell script:
    # the very first line becomes "#!/bin/bash\r" and the interpreter won't
    # be found.
    launcher_script = launcher_script.replace("\r\n", "\n")

    archive = HERE / f"ALumenans-{number}-mac.zip"
    inside = "ALumenans.app"

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zip_:

        def put(path: str, data: bytes, mode: int = REGULAR):
            entry = zipfile.ZipInfo(path, date_time=(2026, 1, 1, 0, 0, 0))
            entry.external_attr = mode
            entry.create_system = 3  # Unix: otherwise permissions are ignored
            entry.compress_type = zipfile.ZIP_DEFLATED
            zip_.writestr(entry, data)

        for directory in (
            f"{inside}/", f"{inside}/Contents/", f"{inside}/Contents/MacOS/",
            f"{inside}/Contents/Resources/", f"{inside}/Contents/Resources/app/",
            f"{inside}/Contents/Resources/app/alumenans/",
        ):
            put(directory, b"", DIRECTORY)

        put(f"{inside}/Contents/Info.plist", plist.encode("utf-8"))
        put(f"{inside}/Contents/PkgInfo", b"APPL????")
        put(
            f"{inside}/Contents/MacOS/ALumenans",
            launcher_script.encode("utf-8"),
            EXECUTABLE,
        )
        put(
            f"{inside}/Contents/Resources/ALumenans.icns",
            icon(PACKAGE_RESOURCES / "ALumenans_logo.png"),
        )
        folders = sorted(
            {parent.as_posix() for name in names for parent in Path(name).parents}
            - {"."}
        )
        for folder in folders:
            put(f"{inside}/Contents/Resources/app/alumenans/{folder}/", b"", DIRECTORY)
        for name in names:
            put(
                f"{inside}/Contents/Resources/app/alumenans/{name}",
                (PACKAGE / name).read_bytes(),
            )

        packages = packages_for_mac()
        # Whatever is in both the runtime and the packages (pip, setuptools) we
        # take from the packages: two entries with the same name in a zip make
        # extraction a lottery.
        site = ("lib", f"python{PYTHON_VERSION}", "site-packages")
        own = frozenset(site + (path.name,) for path in packages.iterdir())
        runtime = python_for_mac()
        count = put_tree(
            zip_, inside, put, runtime, skip=own
        )
        print(f"Python inside: {count} files")
        count = put_tree(
            zip_, inside, put, packages, prefix="/".join(site) + "/",
        )
        print(f"Packages inside: {count} files")
        check_permissions(zip_, inside)

        # Licenses: the program's own (GPLv3), the notices, and the full
        # texts of every component that went in above, collected from the
        # very same folders.
        licenses = RUNTIME_CACHE / "licenses"
        third_party_licenses.collect(licenses, [packages, runtime.joinpath(*site)])
        put(f"{inside}/Contents/Resources/LICENSE.txt", (REPO_ROOT / "LICENSE").read_bytes())
        put(
            f"{inside}/Contents/Resources/THIRD-PARTY-NOTICES.txt",
            (REPO_ROOT / "THIRD-PARTY-NOTICES.md").read_bytes(),
        )
        put(f"{inside}/Contents/Resources/licenses/", b"", DIRECTORY)
        for path in sorted(licenses.rglob("*")):
            name = f"{inside}/Contents/Resources/licenses/{path.relative_to(licenses).as_posix()}"
            if path.is_dir():
                put(name + "/", b"", DIRECTORY)
            else:
                put(name, path.read_bytes())
        print(f"Licenses inside: {sum(1 for p in licenses.rglob('*') if p.is_file())} files")

        # There are deliberately no instructions inside the archive. A text
        # file made on Windows opens unreadable on the Mac, and the person
        # runs into that before even getting to the program. The
        # instructions live separately, as a link to a document — where they
        # can also be corrected without rebuilding the archive.

    return archive


def main() -> int:
    archive = assemble()
    size = archive.stat().st_size / 1024 / 1024
    print(f"Done: {archive}")
    print(f"Size: {size:.1f} MB")
    print()
    print("Give this one file to the person with the Mac.")
    print("It contains only the program itself.")
    print("Send the instructions separately, as a link to a document.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
