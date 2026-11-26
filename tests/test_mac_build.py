#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The Mac build.

Both builds — the Windows installer and the Mac script — take the
alumenans package whole. Neither has a list of files: such lists drift
apart, and one day the Mac would be missing a file everyone forgot about.

We have no developer signature, so launching goes through right-click
→ «Открыть» ("Open"). We check that the person is told this, rather than
it staying in the head of whoever did the build.
"""

import plistlib
import re
import unittest
from pathlib import Path

from alumenans.paths import PACKAGE_DIR, RESOURCES

REPO_ROOT = PACKAGE_DIR.parents[1]
MAC = REPO_ROOT / "packaging" / "macos"
ISS = (REPO_ROOT / "packaging" / "windows" / "ALumenans.iss").read_text(
    encoding="utf-8-sig"
)


def package_files():
    return sorted(
        path.relative_to(PACKAGE_DIR).as_posix()
        for path in PACKAGE_DIR.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )


class BuildTests(unittest.TestCase):
    """The build runs on Windows: we don't have a Mac.

    Both of the usually "Mac-only" parts were worked around. The .icns
    icon is a container with PNGs inside, assembled on the spot. Signing on
    Apple Silicon is required for machine code, but our launcher is a shell
    script, executed by the system bash; there is nothing to sign.

    One thing remains that's easy to get burned on: the "executable"
    permission on the launcher. Without it the app silently won't open."""

    def setUp(self):
        # The file has a hyphen in its name; a plain import can't load it.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "build_mac", MAC / "build-mac.py"
        )
        self.build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.build)

    def test_the_whole_package_goes_inside(self):
        # Like the Windows installer: the whole package, no file list.
        names = self.build.program_files()
        self.assertEqual(names, package_files())
        for wanted in (
            "__main__.py",
            "app.py",
            "transcription/worker.py",
            "resources/anonymizer.html",
            "resources/names-and-organizations.txt",
        ):
            with self.subTest(wanted=wanted):
                self.assertIn(wanted, names)
        self.assertGreater(len(names), 20)

    def test_packages_are_installed_for_the_mac_not_for_this_machine(self):
        """Packages ship inside the app and are installed by cross-install.

        pip with --platform would evaluate `sys_platform == "darwin"`
        markers against the build machine, and pyobjc — without it there's
        no window on the Mac — would silently go missing. Hence uv and an
        explicitly specified target."""

        source_text = (MAC / "build-mac.py").read_text(encoding="utf-8")
        self.assertEqual(self.build.PLATFORM, "aarch64-apple-darwin")
        self.assertIn('"--python-platform", PLATFORM', source_text)
        self.assertIn('"--python-version", PYTHON_VERSION', source_text)
        self.assertIn('"-r", str(PYPROJECT)', source_text)
        self.assertIn("MACOSX_DEPLOYMENT_TARGET=MACOS_MIN", source_text)
        self.assertIn('prefix="/".join(site) + "/"', source_text)

    def test_the_minimum_macos_matches_the_wheels(self):
        # torch 2.13 is built only for macosx_14_0_arm64: declaring less in
        # Info.plist means letting it onto systems where it won't install.
        plist = plistlib.loads((MAC / "Info.plist").read_bytes())
        self.assertEqual(plist["LSMinimumSystemVersion"], self.build.MACOS_MIN)
        self.assertEqual(self.build.MACOS_MIN, "14.0")

    def _packages(self, folder: Path, extra: tuple[str, bytes] | None = None) -> Path:
        for module in self.build.required_modules("darwin"):
            (folder / module).mkdir(parents=True, exist_ok=True)
            (folder / module / "__init__.py").write_text("", encoding="utf-8")
        if extra:
            name, data = extra
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(data)
        return folder

    def test_a_missing_package_stops_the_build(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(SystemExit) as failure:
                self.build.check_packages(Path(folder))
        self.assertIn("gliner", str(failure.exception))

    def test_machine_code_for_another_system_stops_the_build(self):
        """A source-only package would be built by uv here, on Windows.

        For pure Python that's harmless, but a C extension would end up
        built for Windows — and wouldn't load on the Mac."""

        import tempfile

        for name, data in (
            ("torch/_C.pyd", b"MZ\x90\x00"),
            ("numpy/_core/multiarray.so", b"\x7fELF"),
            (
                "something-1.0.dist-info/WHEEL",
                b"Wheel-Version: 1.0\nTag: cp313-cp313-win_amd64\n",
            ),
        ):
            with self.subTest(file=name), tempfile.TemporaryDirectory() as folder:
                root = self._packages(Path(folder), (name, data))
                with self.assertRaises(SystemExit):
                    self.build.check_packages(root)

    def test_mac_machine_code_passes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            root = self._packages(
                Path(folder), ("numpy/_core/multiarray.so", b"\xcf\xfa\xed\xfe")
            )
            self.build.check_packages(root)

    def test_windows_files_inside_a_universal_wheel_pass(self):
        """This is what the real set of packages for the Mac looks like.

        setuptools ships .exe launchers, pywebview ships WebView2 .dll: this
        is data inside generic py3-none-any wheels; on the Mac it's neither
        needed nor harmful. The very first real build tripped on exactly
        this."""

        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            root = self._packages(Path(folder), ("setuptools/cli-64.exe", b"MZ\x90\x00"))
            for name in ("webview/lib/WebBrowserInterop.x64.dll",):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes(b"MZ\x90\x00")
            tag = root / "pywebview-6.2.1.dist-info" / "WHEEL"
            tag.parent.mkdir()
            tag.write_text("Wheel-Version: 1.0\nTag: py3-none-any\n", encoding="utf-8")
            self.build.check_packages(root)

    def test_mac_binaries_without_extension_are_executable(self):
        # ffmpeg inside imageio_ffmpeg is machine code with no extension and
        # not in bin/. Without the "executable" bit the audio can't be read.
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ffmpeg = root / "imageio_ffmpeg" / "binaries" / "ffmpeg-macos-aarch64"
            ffmpeg.parent.mkdir(parents=True)
            ffmpeg.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 16)
            data = root / "imageio_ffmpeg" / "README.txt"
            data.write_text("просто текст", encoding="utf-8")
            self.assertTrue(self.build.is_executable(ffmpeg, root))
            self.assertFalse(self.build.is_executable(data, root))

    def test_runtime_and_packages_do_not_collide(self):
        # Two entries with the same name in a zip make extraction a lottery.
        source_text = (MAC / "build-mac.py").read_text(encoding="utf-8")
        self.assertIn("skip=own", source_text)

    def test_the_freethreaded_build_is_not_taken(self):
        """The build without the global interpreter lock is tagged cp313t.

        Regular wheels don't fit it: torch 2.13 has no cp313t version at
        all, and installation breaks off on someone else's Mac. We already
        built exactly that once."""

        import standalone_python

        release = {"tag_name": "t", "assets": [
            {"name": "cpython-3.13.9+t-aarch64-apple-darwin-freethreaded-install_only.tar.gz",
             "browser_download_url": "ft"},
            {"name": "cpython-3.13.9+t-aarch64-apple-darwin-install_only.tar.gz",
             "browser_download_url": "plain"},
        ]}
        self.assertEqual(
            standalone_python.pick_asset(release, "3.13", "aarch64-apple-darwin")[1], "plain"
        )
        source_text = (MAC / "build-mac.py").read_text(encoding="utf-8")
        self.assertIn('if "freethreaded" not in path.name', source_text)
        # And a check after extraction, in case someone drops it in by hand.
        self.assertIn('list((ready / "lib").glob("python3.*t"))', source_text)

    def test_a_missing_file_stops_the_build(self):
        source_text = (MAC / "build-mac.py").read_text(encoding="utf-8")
        self.assertIn('raise SystemExit(f"Missing files: ', source_text)
        self.assertIn("No files found in src/alumenans", source_text)

    def test_the_launcher_is_marked_executable_inside_the_zip(self):
        source_text = (MAC / "build-mac.py").read_text(encoding="utf-8")
        self.assertIn("EXECUTABLE = 0o100755 << 16", source_text)
        self.assertIn("entry.create_system = 3", source_text)

    def test_the_launcher_loses_windows_line_endings(self):
        # "#!/bin/bash\r" — and the Mac won't find anything to run it with.
        source_text = (MAC / "build-mac.py").read_text(encoding="utf-8")
        self.assertIn('launcher_script.replace("\\r\\n", "\\n")', source_text)

    def test_execute_bits_do_not_come_from_the_filesystem(self):
        """On Windows there is no executable bit at all.

        Permissions there never include it, and the whole runtime shipped
        as ordinary files. On the Mac this looked like "there's no Python
        inside the app" — even though Python was inside. So we decide by
        what the file is, rather than asking the file system."""

        import tempfile

        source_text = (MAC / "build-mac.py").read_text(encoding="utf-8")
        self.assertNotIn("path.stat().st_mode & 0o111", source_text)
        self.assertIn("def is_executable(path: Path, root: Path)", source_text)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "bin").mkdir()
            (root / "lib").mkdir()
            (root / "bin" / "python3").write_bytes(b"\x00\x01\x02")
            (root / "lib" / "libpython3.13.dylib").write_bytes(b"\x00")
            (root / "lib" / "os.py").write_text("x = 1", encoding="utf-8")
            (root / "lib" / "скрипт").write_text("#!/bin/sh\n", encoding="utf-8")
            # No file has execute permission — just like on Windows.
            for path in root.rglob("*"):
                if path.is_file():
                    path.chmod(0o644)

            allowed = {
                path.name: self.build.is_executable(path, root)
                for path in root.rglob("*")
                if path.is_file()
            }
        self.assertTrue(allowed["python3"], "bin/python3 must be executable")
        self.assertTrue(allowed["libpython3.13.dylib"])
        self.assertTrue(allowed["скрипт"], "a file starting with #! is executable")
        self.assertFalse(allowed["os.py"])

    def test_the_build_refuses_an_archive_nobody_can_run(self):
        # A permissions bug shows up only on the Mac and only as "there's no
        # Python inside". We catch it at build time.
        source_text = (MAC / "build-mac.py").read_text(encoding="utf-8")
        self.assertIn("def check_permissions(zip_, inside: str)", source_text)
        self.assertIn("Contents/Resources/python/bin/python3", source_text)
        self.assertIn("was packed without the execute permission", source_text)

    def test_the_runtime_goes_inside_the_app(self):
        # On Windows Python is embedded in the installer; on the Mac it
        # wasn't, and that shifted our problem onto the person.
        source_text = (MAC / "build-mac.py").read_text(encoding="utf-8")
        self.assertIn("def python_for_mac()", source_text)
        self.assertIn("aarch64-apple-darwin", source_text)
        self.assertIn("Contents/Resources/python/", source_text)
        # The "executable" flag gets set, otherwise python3 inside won't
        # start, and there'd be no way to figure out why.
        self.assertIn("EXECUTABLE if allowed else REGULAR", source_text)

    def test_the_icon_is_a_valid_icns(self):
        import struct

        data = self.build.icon(RESOURCES / "ALumenans_logo.png")
        self.assertEqual(data[:4], b"icns")
        length = struct.unpack(">I", data[4:8])[0]
        self.assertEqual(length, len(data))
        pieces, place = [], 8
        while place < length:
            pieces.append(data[place : place + 4].decode("ascii"))
            place += struct.unpack(">I", data[place + 4 : place + 8])[0]
        self.assertIn("ic09", pieces)          # 512×512
        self.assertEqual(len(pieces), len(self.build.SIZES))


class BundleTests(unittest.TestCase):
    def test_the_plist_is_valid_and_carries_the_version(self):
        text = (MAC / "Info.plist").read_text(encoding="utf-8")
        version = re.search(r'#define AppVersion "([^"]+)"', ISS).group(1)
        parsed_name = plistlib.loads(text.replace("VERSION", version).encode("utf-8"))
        self.assertEqual(parsed_name["CFBundleShortVersionString"], version)
        self.assertEqual(parsed_name["CFBundleExecutable"], "ALumenans")
        self.assertEqual(parsed_name["CFBundleIconFile"], "ALumenans.icns")

    def test_only_local_networking_is_allowed(self):
        # The window talks to its own 127.0.0.1 and nowhere else.
        parsed_name = plistlib.loads(
            (MAC / "Info.plist").read_text(encoding="utf-8")
            .replace("VERSION", "0")
            .encode("utf-8")
        )
        security = parsed_name["NSAppTransportSecurity"]
        self.assertTrue(security["NSAllowsLocalNetworking"])
        self.assertNotIn("NSAllowsArbitraryLoads", security)


class ShellNamesTests(unittest.TestCase):
    """In bash a variable name can't be in Cyrillic.

    Such a line is parsed not as an assignment but as a command, and the
    script falls apart on the very first line. `bash -n` doesn't catch it:
    syntactically everything is fine. We've already stepped on this once."""

    LATIN_NAME = re.compile(r"^\s*([A-Za-z_][A-Za-z_0-9]*)=", re.MULTILINE)
    ANY_NAME = re.compile(r"^\s*(\w+)=", re.MULTILINE | re.UNICODE)

    def check(self, file_name):
        text = (MAC / file_name).read_text(encoding="utf-8")
        all_names = set(self.ANY_NAME.findall(text))
        latin_names = set(self.LATIN_NAME.findall(text))
        self.assertEqual(
            sorted(all_names - latin_names), [], f"non-Latin variable names in {file_name}"
        )

    def test_the_launcher_uses_latin_variable_names(self):
        self.check("launcher")


class LauncherTests(unittest.TestCase):
    """Python and all the packages live inside the app.

    There's deliberately no separate environment (venv) here. It added
    state that lived between launches: the first, broken build left
    behind a venv without pip, the new build reused it, and installation
    failed with "No module named pip". Such state shouldn't exist at
    all."""

    def setUp(self):
        self.launcher = (MAC / "launcher").read_text(encoding="utf-8")

    def test_the_bundled_python_is_used(self):
        self.assertIn('python="$resources/python/bin/python3"', self.launcher)
        self.assertIn('exec "$python" -m alumenans', self.launcher)

    def test_there_is_no_virtual_environment(self):
        # The word venv in an explanation of why is fine; a call must not be.
        code = "\n".join(
            w for w in self.launcher.splitlines() if not w.lstrip().startswith("#")
        )
        self.assertNotIn("venv", code)
        self.assertNotIn("окружение", code)

    def test_the_users_own_python_is_not_touched(self):
        for foreign_path in (
            "/Library/Frameworks/Python.framework",
            "/opt/homebrew/bin/python3",
            "/usr/bin/python3",
            "python.org",
        ):
            with self.subTest(foreign_path=foreign_path):
                self.assertNotIn(foreign_path, self.launcher)

    def test_nothing_is_installed_at_launch(self):
        # The packages are already inside the app: there's no longer a
        # folder for extra installs or pip at launch. A leftover of the old
        # way is state between launches, which must not exist.
        code = "\n".join(
            w for w in self.launcher.splitlines() if not w.lstrip().startswith("#")
        )
        self.assertNotIn("ALUMENANS_PACKAGES", code)
        self.assertNotIn("import pip", code)
        self.assertNotIn("Application Support", code)

    def test_child_processes_find_the_package(self):
        self.assertIn('export PYTHONPATH="$app', self.launcher)
        self.assertIn('cd "$app"', self.launcher)


class RuntimeCheckTests(unittest.TestCase):
    """Python's completeness is checked at build time, not on someone's Mac.

    There, any missing piece looks the same: "couldn't prepare the
    program" midway through installation, after half a gigabyte has
    already been downloaded."""

    def test_the_build_checks_what_the_program_needs(self):
        source_text = (MAC / "build-mac.py").read_text(encoding="utf-8")
        self.assertIn("def check_runtime(root: Path)", source_text)
        for wanted in ("pip", "ensurepip", "tkinter", "_ssl", "_sqlite3"):
            with self.subTest(wanted=wanted):
                self.assertIn(f'"{wanted}"', source_text)


class InstructionsTests(unittest.TestCase):
    """The instructions text is written by a person, not a program.

    So what's checked here isn't the wording but two facts we've already
    been burned on: the route through System Settings (in recent macOS,
    right-click doesn't offer the «всё равно» ("Anyway") button) and the
    absence of any requirement to install Python — it's inside the app
    now."""

    def setUp(self):
        self.text = (MAC / "HOW-TO-RUN.txt").read_text(encoding="utf-8")

    def test_the_gatekeeper_path_is_the_modern_one(self):
        self.assertIn("Конфиденциальность и безопасность", self.text)
        self.assertIn("Открыть всё равно", self.text)

    def test_nobody_is_sent_to_install_python(self):
        self.assertNotIn("python.org", self.text)
        self.assertNotIn("universal2", self.text)

    def test_the_main_promise_is_stated(self):
        self.assertIn("Ничего не отправляет", self.text)

    def test_the_instruction_does_not_travel_inside_the_archive(self):
        """A text file from Windows is unreadable on the Mac.

        The person opens it first and runs into mojibake before even
        getting to the program. The instructions live in a separate
        document."""

        build = (MAC / "build-mac.py").read_text(encoding="utf-8")
        start = build.find("def assemble(")
        self.assertGreater(start, 0)
        self.assertNotIn("HOW-TO-RUN.txt", build[start:])


class PlatformTests(unittest.TestCase):
    """What in the program itself depends on the operating system."""

    def test_windows_only_code_switches_itself_off(self):
        source_text = "".join(
            (PACKAGE_DIR / name).read_text(encoding="utf-8")
            for name in ("launch.py", "api.py")
        )
        for guard in (
            'if sys.platform != "win32":',
            'if os.name == "nt":',
        ):
            with self.subTest(guard=guard):
                self.assertIn(guard, source_text)

    def test_models_move_out_of_a_read_only_bundle(self):
        # A regular user can't write to /Applications, and the models weigh
        # half a gigabyte and are downloaded on first launch.
        paths = (PACKAGE_DIR / "paths.py").read_text(encoding="utf-8")
        self.assertIn("def models_home()", paths)
        self.assertIn('"Library" / "Application Support" / "ALumenans"', paths)
        # The window and the worker get the folder from one place, rather
        # than each having its own.
        for name in ("launch.py", "transcription/worker.py"):
            with self.subTest(file=name):
                source_text = (PACKAGE_DIR / name).read_text(encoding="utf-8")
                self.assertIn("models_home()", source_text)

    def test_the_window_asks_for_pyobjc_on_mac_only(self):
        from alumenans.dependencies import required_modules

        self.assertIn("WebKit", required_modules("darwin"))
        self.assertNotIn("WebKit", required_modules("win32"))
        project = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(
            "\"pyobjc-framework-WebKit==11.1; sys_platform == 'darwin'\"", project
        )


if __name__ == "__main__":
    unittest.main()
