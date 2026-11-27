"""Getting ready to start: checking the build, downloading models, the setup window."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from alumenans.dependencies import missing_modules, required_modules
from alumenans.i18n import t
from alumenans.paths import (
    INSTALL_ROOT,
    RESOURCES,
    child_environment,
    models_home,
    state_file,
)
from alumenans.transcription.core import existing_directory
from alumenans.transcription.service import WORKER_MODULE, worker_python

FIRST_RUN_MARK = state_file(".installed", legacy=".установлено")


NL2 = chr(10) * 2


ICON = RESOURCES / "ALumenans_logo.ico"


WINDOWS_APP_ID = "ALumenans.InterviewProcessing"


TRANSCRIBER_MODELS = models_home()


ANONYMIZER_SETUP_ARG = "--prepare-anonymizer-models"


SETUP_WINDOW_ARG = "--setup-window"


REQUIRED_MODULES = required_modules()


def transcriber_models_ready():
    root = TRANSCRIBER_MODELS
    return all(
        (
            (root / "asr" / "v3_e2e_ctc.int8.onnx").is_file(),
            (root / "vad" / "silero_vad.onnx").is_file(),
            (
                root
                / "embedding"
                / "3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx"
            ).is_file(),
        )
    )


def prepare_transcriber_models(progress):
    if transcriber_models_ready():
        return None

    progress(t("launch.loading_transcription_models"))
    environment = child_environment()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    try:
        process = subprocess.Popen(
            [str(worker_python()), "-m", WORKER_MODULE, "prepare"],
            cwd=existing_directory(INSTALL_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert process.stdout is not None
        last_error = None
        for line in process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "progress" and event.get("message"):
                progress(event["message"])
            elif event.get("type") == "error":
                last_error = event.get("message")
        process.stdout.close()
        return_code = process.wait()
        if return_code:
            return str(last_error or t("launch.transcription_models_failed"))[:400]
    except Exception as error:
        return str(error)[:400]

    if not transcriber_models_ready():
        return t("launch.transcription_models_incomplete")
    return None


def prepare_anonymizer_models():
    try:
        process = subprocess.run(
            [str(worker_python()), "-m", "alumenans", ANONYMIZER_SETUP_ARG],
            cwd=existing_directory(INSTALL_ROOT),
            env=child_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as error:
        return str(error)[:400]
    if process.returncode:
        output = (process.stdout or "").strip()
        return output[-400:] or t("launch.anonymizer_models_failed")
    return None


def is_incomplete_build(message) -> bool:
    return str(message or "").startswith(t("launch.incomplete_build"))


def incomplete_build_message(missing):
    """What to say if packages are missing from the build.

    The program cannot and should not fetch them: everything it needs ships
    together with it. So the installation is damaged, and the only fix is to
    reinstall."""

    return (
        t("launch.incomplete_build") + ", ".join(missing) + "."
        + NL2 + t("launch.reinstall")
    )[:400]


def prepare(progress=lambda text: None, *, prepare_anonymizer=False):
    """Loads the models once, in child processes.

    No packages are installed here: they are already in the build."""
    missing = missing_packages()
    if missing:
        return incomplete_build_message(missing)

    if prepare_anonymizer:
        progress(t("launch.preparing_anonymizer"))
        error = prepare_anonymizer_models()
        if error:
            return error

    return prepare_transcriber_models(progress)


def stabilize_wallpaper():
    """Keeps the cached Windows wallpaper if the original file has been deleted."""
    if sys.platform != "win32":
        return

    try:
        import ctypes
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as key:
            source, _ = winreg.QueryValueEx(key, "WallPaper")

        source_path = Path(os.path.expandvars(str(source).strip().strip('"')))
        if source_path.is_file():
            return

        appdata = os.environ.get("APPDATA")
        if not appdata:
            return

        cached = Path(appdata) / "Microsoft" / "Windows" / "Themes" / "TranscodedWallpaper"
        if not cached.is_file() or cached.stat().st_size == 0:
            return

        # SPIF_SENDWININICHANGE refreshes this session; no profile setting is written.
        ctypes.windll.user32.SystemParametersInfoW(20, 0, str(cached), 2)
    except Exception:
        pass


STARTUP_LOG = state_file(".startup.log", legacy=".запуск.log")


def _write_startup_log(lines):
    """Leaves a trace of the launch: without it, the cause of a long wait is invisible."""
    try:
        STARTUP_LOG.write_text(
            time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + "\n".join(lines) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def prepare_launch(progress=lambda text: None):
    """Checks the installation in the launch window without importing the heavy models."""
    started = time.monotonic()
    log = []

    def note(text):
        log.append(f"{time.monotonic() - started:6.1f} s  {text}")

    progress(t("launch.checking_packages"))
    missing = missing_packages()
    note(
        "packages checked: "
        + ("MISSING " + ", ".join(missing) if missing else "all present")
    )
    if missing:
        _write_startup_log(log)
        return incomplete_build_message(missing)

    first_run = not FIRST_RUN_MARK.exists()
    models_ready = transcriber_models_ready()
    note(f"first run: {'yes' if first_run else 'no'}")
    note(f"transcription models: {'present' if models_ready else 'not found'}")

    needs_anonymizer = first_run
    note(
        "anonymization models: "
        + ("being prepared" if needs_anonymizer else "need no preparation")
    )

    if first_run or not models_ready:
        note("STARTING THE LONG PREPARATION")
        error = prepare(progress, prepare_anonymizer=needs_anonymizer)
        note("preparation finished" + (f" with an error: {error}" if error else ""))
        if error:
            _write_startup_log(log)
            return error
        try:
            FIRST_RUN_MARK.write_text("done", encoding="utf-8")
        except Exception:
            pass
    else:
        note("no long preparation needed, skipping")

    progress(t("launch.opening_window"))
    stabilize_wallpaper()
    note("ready, opening the window")
    _write_startup_log(log)
    return None


def configure_windows_identity():
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            WINDOWS_APP_ID
        )
    except Exception:
        pass


def set_tk_icon(window):
    if sys.platform != "win32" or not ICON.exists():
        return
    try:
        window.iconbitmap(default=str(ICON))
    except Exception:
        pass


def setup_window(status_path=None, close_path=None):
    """The preparation window, in tkinter — it ships with Python, so the
    window appears before anything is installed: the person never sees a
    console or a browser. If the window can't be created, we prepare silently."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return prepare_launch()

    try:
        root = tk.Tk()
    except Exception:
        return prepare_launch()

    root.title("ALumenans")
    set_tk_icon(root)
    root.configure(bg="#EDEEE9")
    root.resizable(False, False)
    width, height = 470, 215
    root.geometry(f"{width}x{height}"
                  f"+{(root.winfo_screenwidth()-width)//2}"
                  f"+{(root.winfo_screenheight()-height)//2}")

    tk.Label(root, text=t("launch.title"), bg="#EDEEE9", fg="#1B1E1A",
             font=("Segoe UI", 14)).pack(pady=(30, 6))
    tk.Label(root, text=t("launch.subtitle"),
             bg="#EDEEE9", fg="#5E645C", font=("Segoe UI", 9)).pack()
    step = tk.Label(root, text="", bg="#EDEEE9", fg="#1B1E1A",
                    font=("Segoe UI", 10), wraplength=410, justify="center")
    step.pack(pady=(22, 0))
    bar = ttk.Progressbar(root, mode="indeterminate", length=340)
    bar.pack(pady=(16, 0))
    bar.start(14)

    outcome = {}
    progress_state = {"text": t("launch.checking")}
    finished = threading.Event()
    shown_at = time.monotonic()

    if status_path is not None and close_path is not None:
        status_path = Path(status_path)
        close_path = Path(close_path)
        last_text = None

        def poll_external():
            nonlocal last_text
            if close_path.is_file():
                remaining = max(
                    0,
                    500 - int((time.monotonic() - shown_at) * 1000),
                )
                root.after(remaining, root.destroy)
                return
            try:
                current = status_path.read_text(encoding="utf-8").strip()
            except OSError:
                current = ""
            if current and current != last_text:
                last_text = current
                step.config(text=current)
            root.after(80, poll_external)

        root.after(50, poll_external)
        root.mainloop()
        return None

    def job():
        try:
            outcome["error"] = prepare_launch(
                lambda text: progress_state.__setitem__("text", text)
            )
        except Exception as error:
            outcome["error"] = str(error)[:400] or type(error).__name__
        finally:
            finished.set()

    def poll():
        step.config(text=progress_state["text"])
        if finished.is_set():
            remaining = max(0, 500 - int((time.monotonic() - shown_at) * 1000))
            root.after(remaining, root.destroy)
            return
        root.after(50, poll)

    root.after(50, lambda: threading.Thread(target=job, daemon=True).start())
    root.after(50, poll)
    root.mainloop()
    return outcome.get("error")


def _write_setup_status(handle, text):
    if not handle:
        return
    try:
        handle["status"].write_text(str(text), encoding="utf-8")
    except OSError:
        pass


def start_setup_window_process():
    try:
        folder = Path(tempfile.mkdtemp(prefix="alumenans-launch-"))
        status = folder / "status.txt"
        close = folder / "close"
        status.write_text(t("launch.checking"), encoding="utf-8")
        process = subprocess.Popen(
            [
                str(sys.executable),
                "-m",
                "alumenans",
                SETUP_WINDOW_ARG,
                str(status),
                str(close),
            ],
            cwd=existing_directory(INSTALL_ROOT),
            env=child_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {
            "folder": folder,
            "status": status,
            "close": close,
            "process": process,
        }
    except Exception:
        return None


def signal_setup_window_close(handle):
    if not handle:
        return
    try:
        handle["close"].touch(exist_ok=True)
    except OSError:
        pass


def cleanup_setup_window_process(handle):
    if not handle:
        return
    signal_setup_window_close(handle)
    process = handle.get("process")
    if process is not None:
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
    for key in ("status", "close"):
        try:
            handle[key].unlink(missing_ok=True)
        except OSError:
            pass
    try:
        handle["folder"].rmdir()
    except OSError:
        pass


def missing_packages():
    """Modules from REQUIRED_MODULES that are missing from this build."""
    return missing_modules()

