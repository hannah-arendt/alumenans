"""What the window can ask of Python: `AppApi`, exposed as `window.pywebview.api`."""

import json
import os
import re
import threading
import urllib.parse
from pathlib import Path

from alumenans import i18n
from alumenans.cleanup import unanonymised_files
from alumenans.current_audio import AUDIO_STATE, audio_parts, set_current_audio
from alumenans.detection.subject import set_subject_terms
from alumenans.i18n import t
from alumenans.interview_name import group_parts, parse_interview_name, study_title_from
from alumenans.paths import state_file
from alumenans.proofreading import set_study_words
from alumenans.storage import cipher
from alumenans.storage.cipher import StudySecret
from alumenans.storage.exchange import (
    ExchangeError,
    STUDY_FILE_NAME,
    build_export,
    export_name,
    read_study_file,
)
from alumenans.storage.project_store import (
    ProjectError,
    ProjectStore,
    interview_stem,
    project_path_for,
    project_summaries,
)
from alumenans.storage.study_store import StudyError, StudyStore, study_path_for
from alumenans.study_documents import classify_documents, document_kind, read_document
from alumenans.subject_terms import suggest_from_documents
from alumenans.transcription.core import (
    TranscriptionError,
    probe_media_duration,
    validate_media_path,
    validate_speaker_count,
)

RESEARCHER_FILE = state_file(".researcher", legacy=".исследователь")


MEDIA_SUFFIXES = {".mp3", ".wav", ".wma", ".mp4", ".m4a", ".ogg"}


def _refresh_study_words(words):
    """The study dictionary — into proofreading. The one place where it is read."""

    return set_study_words(words or ())


def _open_new_words_email_window(path, words):
    recipient = "nelepelniza@gmail.com"
    subject = "Новые слова для словаря ALumenans"
    body = (
        "Здравствуйте!\n\n"
        "Прошу добавить эти словоформы в словарь ALumenans:\n\n"
        + "\n".join(words)
    )

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class MapiRecipDescW(ctypes.Structure):
                _fields_ = [
                    ("ulReserved", wintypes.ULONG),
                    ("ulRecipClass", wintypes.ULONG),
                    ("lpszName", wintypes.LPWSTR),
                    ("lpszAddress", wintypes.LPWSTR),
                    ("ulEIDSize", wintypes.ULONG),
                    ("lpEntryID", wintypes.LPVOID),
                ]

            class MapiFileDescW(ctypes.Structure):
                _fields_ = [
                    ("ulReserved", wintypes.ULONG),
                    ("flFlags", wintypes.ULONG),
                    ("nPosition", wintypes.ULONG),
                    ("lpszPathName", wintypes.LPWSTR),
                    ("lpszFileName", wintypes.LPWSTR),
                    ("lpFileType", wintypes.LPVOID),
                ]

            class MapiMessageW(ctypes.Structure):
                _fields_ = [
                    ("ulReserved", wintypes.ULONG),
                    ("lpszSubject", wintypes.LPWSTR),
                    ("lpszNoteText", wintypes.LPWSTR),
                    ("lpszMessageType", wintypes.LPWSTR),
                    ("lpszDateReceived", wintypes.LPWSTR),
                    ("lpszConversationID", wintypes.LPWSTR),
                    ("flFlags", wintypes.ULONG),
                    ("lpOriginator", wintypes.LPVOID),
                    ("nRecipCount", wintypes.ULONG),
                    ("lpRecips", ctypes.POINTER(MapiRecipDescW)),
                    ("nFileCount", wintypes.ULONG),
                    ("lpFiles", ctypes.POINTER(MapiFileDescW)),
                ]

            recipient_data = MapiRecipDescW(
                0, 1, recipient, f"SMTP:{recipient}", 0, None
            )
            file_data = MapiFileDescW(
                0, 0, 0xFFFFFFFF, str(path), path.name, None
            )
            message = MapiMessageW(
                0,
                subject,
                body,
                None,
                None,
                None,
                0,
                None,
                1,
                ctypes.pointer(recipient_data),
                1,
                ctypes.pointer(file_data),
            )
            result = ctypes.windll.mapi32.MAPISendMailW(
                0,
                0,
                ctypes.byref(message),
                0x00000008 | 0x00000001,
                0,
            )
            if result in {0, 1}:
                return
        except Exception:
            pass

        query = urllib.parse.urlencode(
            {"subject": subject, "body": body},
            quote_via=urllib.parse.quote,
        )
        os.startfile(f"mailto:{recipient}?{query}")


def _schedule_new_words_email(path, words):
    threading.Thread(
        target=_open_new_words_email_window,
        args=(path, words),
        daemon=True,
        name="new-words-email",
    ).start()


class AppApi:
    def __init__(self, transcriber, ready_callback=None):
        self.transcriber = transcriber
        self.window = None
        self.selected_media = None
        self._ready_callback = ready_callback
        self._interface_ready = False
        self._ready_lock = threading.Lock()
        self._project = None
        self._interview_id = ""
        self._project_pinned = False
        self.media_parts = []
        # Re-entrant locks: an ordinary lock would deadlock on a nested call.
        self._project_lock = threading.RLock()
        self._study = None
        self._study_id = ""
        self._study_folder = None
        # The key of the open study, in memory only. None means locked.
        self._study_secret = None
        self._unreadable_projects = []
        self._study_lock = threading.RLock()

    # -- project file ----------------------------------------------------

    def _study_gate(self):
        """An error reply until a study folder is chosen and unlocked, else None.

        Every interview file is encrypted with the study's password, so
        nothing can be read or written before the study is unlocked, and
        nothing is ever written next to the recording."""

        if self._study_folder is None:
            return {"ok": False, "error": t("api.choose_study_first")}
        if self._study_secret is None:
            return {"ok": False, "error": t("api.study_locked")}
        return None

    def _project_home(self):
        """The folder where the project files live: the unlocked study's.

        Not the recording's folder: the audio file gets deleted, the work
        doesn't."""

        if self._study_folder is None or self._study_secret is None:
            return None
        return Path(self._study_folder)

    def _project_store(self, *, create=True):
        """The project file is opened on demand.

        Switching recordings must close the previous project: otherwise the
        edits to one interview would quietly end up in another one's file."""

        if self.selected_media is None:
            # The work may have been opened without an audio file: it has
            # been deleted, but proofreading goes on. There is no reason to
            # close such a project.
            if self._project is not None and self._project_pinned:
                return self._project
            self._close_project()
            return None
        home = self._project_home()
        if home is None:
            self._close_project()
            return None
        target = project_path_for(self.selected_media, home)
        if self._project is not None and self._project.path == target:
            return self._project
        self._close_project()
        if not create and not target.is_file():
            return None
        self._project = ProjectStore(target, self._study_secret)
        self._project.connect()
        return self._project

    def _close_project(self):
        if self._project is not None:
            try:
                self._project.close()
            except Exception:
                pass
        self._project = None
        self._interview_id = ""
        self._project_pinned = False

    def project_save_transcript(self, result, guide="", notes="", speaker_count=None):
        """Puts a fresh transcript into the project file next to the recording."""

        with self._project_lock:
            gate = self._study_gate()
            if gate is not None:
                return gate
            store = self._project_store()
            if store is None:
                return {"ok": False, "error": t("api.no_source_audio")}
            try:
                self._interview_id = store.save_transcript(
                    result if isinstance(result, dict) else {},
                    source_path=self.selected_media,
                    guide=str(guide or ""),
                    notes=str(notes or ""),
                    speaker_count=speaker_count,
                )
            except (ProjectError, Exception) as error:
                return {"ok": False, "error": str(error)[:400]}
            return {
                "ok": True,
                "interview_id": self._interview_id,
                "path": str(store.path),
            }

    def project_save_turns(self, turns, guide=None, notes=None):
        """Autosave for proofreading: turns, topics and context."""

        with self._project_lock:
            store = self._project_store()
            if store is None or not self._interview_id:
                return {"ok": False, "error": t("api.no_project")}
            try:
                saved = store.save_turns(
                    self._interview_id,
                    turns if isinstance(turns, list) else [],
                )
                if guide is not None or notes is not None:
                    store.save_context(
                        self._interview_id,
                        guide=None if guide is None else str(guide),
                        notes=None if notes is None else str(notes),
                    )
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True, "turns": saved, "path": str(store.path)}

    def project_record_decision(self, decision):
        with self._project_lock:
            store = self._project_store()
            if store is None or not self._interview_id:
                return {"ok": False, "error": t("api.no_project")}
            payload = decision if isinstance(decision, dict) else {}
            try:
                store.record_decision(
                    self._interview_id,
                    turn_id=str(payload.get("turn_id") or ""),
                    kind=str(payload.get("kind") or ""),
                    fragment=str(payload.get("fragment") or ""),
                    action=str(payload.get("action") or ""),
                    replacement=str(payload.get("replacement") or ""),
                )
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True}

    def _keep_key_archive(self, archive, entries):
        """Puts the encrypted key into the project — the very same archive.

        The key never goes into the project file in plain text at all: such a
        file gets passed to a colleague, and a key inside it would undo the
        anonymisation entirely."""

        with self._project_lock:
            store = self._project_store()
            if store is None or not self._interview_id:
                return False
            try:
                store.save_anonymization_key(
                    self._interview_id, archive, entries=int(entries or 0)
                )
            except Exception:
                return False
            return True

    # -- study -----------------------------------------------------------

    def _study_store(self, *, create=True):
        """The study file lives in the folder the person chose, and opens
        only once the study is unlocked."""

        folder = self._study_folder
        if folder is None or self._study_secret is None:
            return None
        target = study_path_for(folder)
        if self._study is not None and self._study.path == target:
            return self._study
        self._close_study()
        if not create and not target.is_file():
            return None
        self._study = StudyStore(target, self._study_secret)
        self._study.connect()
        self._study_id = self._study.ensure_study()
        self._refresh_subject_terms()
        return self._study

    def _close_study(self):
        if self._study is not None:
            try:
                self._study.close()
            except Exception:
                pass
        self._study = None
        self._study_id = ""
        set_subject_terms(())

    def _refresh_subject_terms(self):
        """Moves the confirmed terms to where the search for findings can see them."""

        if self._study is None or not self._study_id:
            set_subject_terms(())
            return []
        terms = self._study.subject_terms(self._study_id, confirmed_only=True)
        set_subject_terms([item["term"] for item in terms])
        return terms

    def _study_interviews(self, found):
        """The list of the series' interviews: first the work, then the recordings.

        The list used to be built from the folder's contents, and that was a
        mistake. A recording is a consumable: it is deleted once the interview
        has been processed, and the interview would vanish from the first
        screen along with the audio file, even though all the work on it lay
        intact on disk. So the basis of the list is the project files, and the
        recordings found only supplement it with the interviews that have not
        been started yet."""

        entries = {}
        order = []

        def slot(key):
            if key not in entries:
                entries[key] = {
                    "key": key,
                    "paths": [],
                    "name": "",
                    "label": "",
                    "interviewer": "",
                    "number": "",
                    "respondent": "",
                    "parts": 0,
                    "audio": False,
                    "project": "",
                    "turns": 0,
                    "edited": 0,
                    "anonymized": 0,
                    "duration": 0.0,
                }
                order.append(key)
            return entries[key]

        def describe(item, source):
            parsed = parse_interview_name(source)
            item["name"] = item["name"] or Path(str(source)).name
            item["label"] = item["label"] or parsed.label
            item["interviewer"] = item["interviewer"] or parsed.interviewer
            item["number"] = item["number"] or parsed.number
            item["respondent"] = item["respondent"] or parsed.respondent

        summaries, unreadable = project_summaries(self._study_folder, self._study_secret)
        self._unreadable_projects = [
            t("api.unreadable_interview", name=item["name"], error=item["error"])
            for item in unreadable
        ]
        for summary in summaries:
            source = summary["source_name"] or Path(summary["path"]).stem
            item = slot(parse_interview_name(source).key)
            describe(item, source)
            item["project"] = summary["path"]
            item["turns"] = summary["turns"]
            item["edited"] = summary["edited"]
            item["anonymized"] = summary["anonymized"]
            item["duration"] = summary["duration"]

        for group in group_parts(found):
            item = slot(parse_interview_name(group[0]).key)
            describe(item, group[0])
            item["paths"] = [str(path) for path in group]
            item["parts"] = len(group)
            item["audio"] = True

        def rank(key):
            item = entries[key]
            digits = re.sub(r"\D", "", item["number"])
            return (0, int(digits)) if digits else (1, 0)

        return [entries[key] for key in sorted(order, key=lambda key: (rank(key), entries[key]["label"]))]

    def study_open(self, folder=""):
        """Chooses the study folder. Without a path, opens the folder picker.

        Nothing is read or written yet: the study opens with its password,
        through study_unlock, or is started with a new one, through
        study_create. The reply says which of the two the folder needs."""

        if not folder:
            if self.window is None:
                return {"ok": False, "error": t("api.window_not_ready")}
            try:
                import webview

                selected = self.window.create_file_dialog(webview.FileDialog.FOLDER)
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            if not selected:
                return {"ok": True, "cancelled": True}
            folder = self._chosen_path(selected)
        chosen = Path(folder).expanduser()
        if not chosen.is_dir():
            return {"ok": False, "error": t("api.folder_open_failed")}
        if self.transcriber.status()["status"] in {"running", "cancelling"}:
            return {"ok": False, "error": t("api.stop_transcription_first")}
        # Project first, then study: the order export_interview takes them in.
        with self._project_lock:
            self._close_project()
            self.selected_media = None
            self.media_parts = []
            set_current_audio(None)
        with self._study_lock:
            self._close_study()
            self._study_secret = None
            self._study_folder = chosen
        return {
            "ok": True,
            "cancelled": False,
            "folder": str(chosen),
            "exists": study_path_for(chosen).is_file(),
            "locked": True,
        }

    def study_unlock(self, password=""):
        """Opens the chosen study with its password."""

        with self._study_lock:
            if self._study_folder is None:
                return {"ok": False, "error": t("api.choose_study_first")}
            target = study_path_for(self._study_folder)
            if not target.is_file():
                return {"ok": False, "error": t("study.no_study_file")}
            try:
                secret = StudySecret.for_file(target, str(password or ""))
                # A trial open: a wrong password must not leave the study
                # half-unlocked.
                cipher.connect(target, secret).close()
            except cipher.WrongPassword as error:
                return {"ok": False, "wrong_password": True, "error": str(error)}
            except cipher.CipherError as error:
                return {"ok": False, "error": str(error)}
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            self._study_secret = secret
            return self._study_payload()

    def study_create(self, password=""):
        """Starts a new study in the chosen folder, protected by a new password."""

        with self._study_lock:
            if self._study_folder is None:
                return {"ok": False, "error": t("api.choose_study_first")}
            if study_path_for(self._study_folder).is_file():
                return {"ok": False, "error": t("study.already_exists")}
            password = str(password or "")
            if len(password) < cipher.MIN_PASSWORD_LENGTH:
                return {
                    "ok": False,
                    "error": t("study.password_too_short", n=cipher.MIN_PASSWORD_LENGTH),
                }
            self._study_secret = StudySecret.create(password)
            reply = self._study_payload()
            if not reply.get("ok"):
                self._close_study()
                self._study_secret = None
            return reply

    def _study_payload(self):
        """Everything the window shows about the unlocked study."""

        with self._study_lock:
            try:
                store = self._study_store()
                if store is None:
                    return {"ok": False, "error": t("api.folder_open_failed")}
                state = store.load_study() or {}
                found = sorted(
                    item
                    for item in self._study_folder.iterdir()
                    if item.suffix.lower() in MEDIA_SUFFIXES
                )
                recordings = self._study_interviews(found)
                # The file name already contains the series designation —
                # there is no point asking for it separately.
                if not state.get("title"):
                    guessed = study_title_from(
                        [item["name"] for item in recordings if item["name"]]
                    )
                    if guessed:
                        store.save_study(self._study_id, title=guessed)
                        state["title"] = guessed
                picked_up = self._pick_up_documents(store, state, recordings)
                # The file put together by the lead is read automatically:
                # the person has no reason to remember that it needs
                # "updating".
                if (self._study_folder / STUDY_FILE_NAME).is_file():
                    self._import_study_file(store, state)
                self._remember_folder(self._study_folder)
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {
                "ok": True,
                "cancelled": False,
                "folder": str(self._study_folder),
                "path": str(store.path),
                "recordings": recordings,
                # Gaps in the numbering are not an error: a sociologist's
                # device holds only their own interviews. Interview files
                # the password doesn't open are: they may be whole
                # interviews, and must not vanish from the list silently.
                "problems": list(self._unreadable_projects),
                "title": state.get("title", ""),
                "guide": state.get("guide", ""),
                "programme": state.get("programme", ""),
                "interviews": state.get("interviews", []),
                "terms": state.get("subject_terms", []),
                "documents": store.documents(self._study_id),
                "picked_up": picked_up,
                "version": store.version(self._study_id),
                "words": store.words(self._study_id),
                # The team buttons are needed only by those working from
                # the lead's file. A solo transcription does without them.
                "team": bool(store.version(self._study_id).get("version")),
            }

    def _pick_up_documents(self, store, state, recordings):
        """Picks up from the folder what the person doesn't need to be shown again.

        The guide and the programme are shared across the study; notes are
        tied to an interview by the audio file's name. A guide that has
        already been entered is not overwritten: the person may have corrected
        it in the window, and the file in the folder does not take precedence
        here."""

        if self._study_folder is None:
            return []
        stems = [
            Path(entry["name"]).stem
            for entry in recordings
            if entry.get("name")
        ]
        try:
            names = [
                item.name
                for item in self._study_folder.iterdir()
                if item.is_file()
            ]
        except OSError:
            return []

        found = classify_documents(names, stems)
        picked_up = []

        for field in ("guide", "programme"):
            name = found[field]
            if not name or state.get(field):
                continue
            text = self._read_study_file(name)
            if not text:
                continue
            store.save_study(self._study_id, **{field: text})
            state[field] = text
            picked_up.append({"name": name, "kind": field})

        for stem, name in found["notes"].items():
            text = self._read_study_file(name)
            if not text:
                continue
            store.save_document(
                self._study_id,
                filename=name,
                text=text,
                kind="notes",
                title=stem,
                interview_id=stem,
            )
            picked_up.append({"name": name, "kind": "notes", "interview": stem})

        for document in found["documents"]:
            text = self._read_study_file(document["name"])
            if not text:
                continue
            store.save_document(
                self._study_id,
                filename=document["name"],
                text=text,
                kind=document["kind"],
            )
            picked_up.append(document)
        return picked_up

    def _read_study_file(self, name):
        try:
            return read_document(self._study_folder / name).strip()
        except Exception:
            return ""

    def study_add_document(self, kind="document"):
        """Loads a study document that lives outside the series folder."""

        with self._study_lock:
            store = self._study_store()
            if store is None:
                return {"ok": False, "error": t("api.choose_study_first")}
            if self.window is None:
                return {"ok": False, "error": t("api.window_not_ready")}
            try:
                import webview

                selected = self.window.create_file_dialog(
                    webview.FileDialog.OPEN,
                    allow_multiple=True,
                    file_types=(
                        t("api.filetype_documents"),
                        t("api.filetype_all"),
                    ),
                )
                if not selected:
                    return {"ok": True, "cancelled": True}
                added = []
                for target in self._chosen_paths(selected):
                    text = read_document(target).strip()
                    if not text:
                        continue
                    store.save_document(
                        self._study_id,
                        filename=target.name,
                        text=text,
                        kind=str(kind or "document") if kind != "auto"
                        else document_kind(target.name),
                    )
                    added.append(target.name)
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {
                "ok": True,
                "cancelled": False,
                "added": added,
                "documents": store.documents(self._study_id),
            }

    def study_documents(self, interview=None):
        with self._study_lock:
            store = self._study_store(create=False)
            if store is None:
                return {"ok": True, "documents": []}
            return {
                "ok": True,
                "documents": store.documents(self._study_id, interview),
            }

    def study_remove_document(self, document_id):
        with self._study_lock:
            store = self._study_store(create=False)
            if store is None:
                return {"ok": False, "error": t("api.study_not_open")}
            store.remove_document(str(document_id))
            return {"ok": True, "documents": store.documents(self._study_id)}

    # -- study dictionary ------------------------------------------------

    def study_add_word(self, word):
        """A word from the field — «задонатить» ("to donate", slang),
        «отписка» ("unsubscribing") — into the study dictionary.

        This is a recognition dictionary, not a dictionary of names: it
        silences a proofreading remark and gives the recogniser a hint, but
        it has nothing to do with anonymisation. Names and titles live in a
        different list."""

        with self._study_lock:
            store = self._study_store()
            if store is None:
                return {"ok": False, "error": t("api.choose_study_first")}
            try:
                added = store.add_word(self._study_id, word)
                words = store.words(self._study_id)
            except StudyError as error:
                return {"ok": False, "error": str(error)}
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
        _refresh_study_words(words)
        return {"ok": True, "word": added, "words": words}

    def study_words(self):
        with self._study_lock:
            store = self._study_store(create=False)
            words = store.words(self._study_id) if store else []
        _refresh_study_words(words)
        return {"ok": True, "words": words}

    def study_remove_word(self, word):
        with self._study_lock:
            store = self._study_store(create=False)
            if store is None:
                return {"ok": False, "error": t("api.study_not_open")}
            store.remove_word(self._study_id, word)
            words = store.words(self._study_id)
        _refresh_study_words(words)
        return {"ok": True, "words": words}

    # -- study file from the lead --------------------------------------------

    def _import_study_file(self, store, state):
        """Reads the file put together by the lead and puts everything in its place."""

        destination = self._study_folder / STUDY_FILE_NAME
        data = read_study_file(destination)
        store.save_study(
            self._study_id,
            title=data["title"],
            guide=data["guide"],
            programme=data["programme"],
        )
        store.save_version(
            self._study_id,
            version=data["version"],
            issued_at=data["issued_at"],
            issued_by=data["issued_by"],
            inbox=data["inbox"],
        )
        for word in data["words"]:
            store.add_word(self._study_id, word, source="исследование")
        for term in data["terms"]:
            store.set_subject_term(self._study_id, term, confirmed=True)
        for document in data["documents"]:
            if document["text"]:
                store.save_document(
                    self._study_id,
                    filename=document["name"],
                    text=document["text"],
                    kind=document["kind"],
                )
        state.update(
            {
                "title": data["title"],
                "guide": data["guide"] or state.get("guide", ""),
                "programme": data["programme"] or state.get("programme", ""),
            }
        )
        self._refresh_subject_terms()
        _refresh_study_words(store.words(self._study_id))
        return data

    def study_version(self):
        with self._study_lock:
            store = self._study_store(create=False)
            if store is None:
                return {"ok": True, "version": {}}
            return {"ok": True, "version": store.version(self._study_id)}

    # -- interview export ----------------------------------------------------

    def export_interview(self):
        """Puts the finished interview into the study folder as a single file.

        The program only writes the file. It is sent by a separate script
        that the lead put in the same folder: the network is none of the
        program's business."""

        with self._project_lock:
            store = self._project_store(create=False)
            if store is None or not self._interview_id:
                return {"ok": False, "error": t("api.interview_not_open")}
            try:
                state = store.anonymization_state(self._interview_id)
                if not state["done"]:
                    return {"ok": False, "error": state["reason"]}
                turns = store.anonymized_turns(self._interview_id)
                interview = store.load(self._interview_id) or {}
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}

        with self._study_lock:
            study_store = self._study_store(create=False)
            version = study_store.version(self._study_id) if study_store else {}
            header = (study_store.load_study() or {}) if study_store else {}
        folder = self._study_folder
        if folder is None:
            return {"ok": False, "error": t("api.no_study_folder")}

        parsed_name = parse_interview_name(
            interview.get("source_name") or (self.selected_media or "")
        )
        try:
            export = build_export(
                study={
                    "title": header.get("title", ""),
                    "version": version.get("version", ""),
                    "issued_at": version.get("issued_at", ""),
                },
                interview={
                    "key": interview_stem(
                        interview.get("source_name") or (self.selected_media or "")
                    ),
                    "study": parsed_name.study,
                    "interviewer": parsed_name.interviewer,
                    "number": parsed_name.number,
                    "respondent": parsed_name.respondent,
                    "duration": interview.get("duration") or 0.0,
                    "moderator": interview.get("moderator_speaker"),
                    "anonymized_at": state.get("anonymized_at"),
                },
                turns=turns,
                notes=interview.get("notes") or "",
                coder=self.researcher_name(),
            )
            destination = folder / export_name(export["interview"]["key"])
            destination.write_text(
                json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except ExchangeError as error:
            return {"ok": False, "error": str(error)}
        except Exception as error:
            return {"ok": False, "error": str(error)[:400]}
        return {
            "ok": True,
            "path": str(destination),
            "name": destination.name,
            "turns": len(export["turns"]),
            "inbox": version.get("inbox", ""),
        }

    def study_save_documents(self, title="", guide="", programme=""):
        """The guide and the programme belong to the study, not to an individual recording."""

        with self._study_lock:
            store = self._study_store()
            if store is None:
                return {"ok": False, "error": t("api.choose_study_first")}
            try:
                store.save_study(
                    self._study_id,
                    title=str(title or ""),
                    guide=str(guide or ""),
                    programme=str(programme or ""),
                )
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True, "path": str(store.path)}

    def study_register(self, branch="", branch_title="", attributes=None):
        """Registers the interview in the series together with its branch and case card."""

        with self._study_lock:
            store = self._study_store()
            if store is None or self.selected_media is None:
                return {"ok": False, "error": t("api.no_recording_or_study")}
            if not self._interview_id:
                return {"ok": False, "error": t("api.no_interview_project")}
            try:
                parsed = parse_interview_name(self.selected_media)
                store.register_interview(
                    self._study_id,
                    interview_id=self._interview_id,
                    label=parsed.label,
                    source_name=Path(self.selected_media).name,
                    source_path=str(self.selected_media),
                    project_path=str(
                        project_path_for(self.selected_media, self._project_home())
                    ),
                    duration=probe_media_duration(self.selected_media) or 0.0,
                )
                if branch:
                    store.save_branch(self._interview_id, branch, branch_title)
                if attributes:
                    store.save_case_attributes(self._interview_id, attributes)
                interviews = store.interviews(self._study_id)
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True, "interviews": interviews}

    LAST_FOLDER = state_file(".last-folder", legacy=".последняя-папка")

    def _remember_folder(self, folder):
        """The study folder is chosen once, not on every launch."""

        try:
            self.LAST_FOLDER.write_text(str(folder), encoding="utf-8")
        except OSError:
            pass

    def last_study_folder(self):
        """The study opened last time, offered again at the next launch.

        Only a folder that still holds a study: an empty or vanished one is
        nothing to reopen."""

        try:
            path = self.LAST_FOLDER.read_text(encoding="utf-8").strip()
        except OSError:
            path = ""
        if not path or not study_path_for(path).is_file():
            return {"ok": True, "folder": ""}
        return {"ok": True, "folder": path, "name": Path(path).name}

    def researcher_name(self):
        path = RESEARCHER_FILE
        try:
            return path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        except OSError:
            return ""

    def settings(self, researcher=None):
        """The researcher's name: exported interviews are signed with it."""

        path = RESEARCHER_FILE
        try:
            if researcher is not None:
                path.write_text(str(researcher).strip()[:80], encoding="utf-8")
            current = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        except OSError as error:
            return {"ok": False, "error": str(error)[:200]}
        return {"ok": True, "researcher": current}

    def study_terms(self):
        """What has been suggested and what has been confirmed as the subject of the study."""

        with self._study_lock:
            store = self._study_store(create=False)
            if store is None:
                return {"ok": True, "terms": [], "study": ""}
            try:
                terms = store.subject_terms(self._study_id)
                self._refresh_subject_terms()
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True, "terms": terms, "study": str(store.path)}

    def study_suggest_terms(self, guide="", programme=""):
        """Collects candidates from the guide and the programme. Confirms nothing."""

        with self._study_lock:
            store = self._study_store()
            if store is None:
                return {"ok": False, "error": t("api.choose_recording_first")}
            try:
                store.save_study(
                    self._study_id,
                    guide=str(guide or ""),
                    programme=str(programme or ""),
                )
                store.suggest_subject_terms(
                    self._study_id,
                    suggest_from_documents(str(guide or ""), str(programme or "")),
                )
                terms = store.subject_terms(self._study_id)
                self._refresh_subject_terms()
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True, "terms": terms}

    def study_set_term(self, term, confirmed):
        """Confirms protection for a single term, or removes it."""

        with self._study_lock:
            store = self._study_store()
            if store is None:
                return {"ok": False, "error": t("api.choose_recording_first")}
            try:
                store.set_subject_term(
                    self._study_id, str(term or ""), confirmed=bool(confirmed)
                )
                terms = self._refresh_subject_terms()
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True, "protected": [item["term"] for item in terms]}

    def study_remove_term(self, term):
        with self._study_lock:
            store = self._study_store()
            if store is None:
                return {"ok": False, "error": t("api.choose_recording_first")}
            try:
                store.remove_subject_term(self._study_id, str(term or ""))
                self._refresh_subject_terms()
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True}

    def project_load(self):
        """Returns unfinished work on the selected recording, if there is any."""

        with self._project_lock:
            store = self._project_store(create=False)
            if store is None:
                return {"ok": True, "state": None}
            try:
                state = store.load()
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            if state:
                self._interview_id = state["interview_id"]
            return {"ok": True, "state": state, "path": str(store.path)}

    def project_mark_anonymized(self, left=0, turns=None):
        """Marks the anonymisation as finished.

        Along with the mark comes the anonymised text, turn by turn: that is
        what gets exported, not the original."""

        with self._project_lock:
            store = self._project_store()
            if store is None or not self._interview_id:
                return {"ok": False, "error": t("api.no_project")}
            try:
                if isinstance(turns, list) and turns:
                    store.save_anonymized_turns(self._interview_id, turns)
                store.mark_anonymized(self._interview_id, left=left)
                state = store.anonymization_state(self._interview_id)
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}
            return {"ok": True, "state": state}

    def cleanup_preview(self):
        """What would be left of the non-anonymised text if it were removed.

        Shown as a list before deletion: deletion is irreversible, and our
        own rule "we never change anything silently" matters most of all
        here — this is the most silent change the program could possibly
        make."""

        with self._project_lock:
            store = self._project_store(create=False)
            if store is None or not self._interview_id:
                return {"ok": True, "ready": False, "files": [], "inside": {}}
            try:
                state = store.anonymization_state(self._interview_id)
                inside = store.original_text_traces(self._interview_id)
                ready = bool(state["done"]) and bool(
                    store.anonymized_turns(self._interview_id)
                )
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}

        files = [str(path) for path in self._own_leftovers()]
        # Words don't count: they are not deleted but transplanted onto the
        # anonymised text, and after the cleanup they stay where they are.
        remaining = bool(files) or bool(inside["turns"]) or bool(inside["decisions"])
        return {
            "ok": True,
            "ready": ready,
            "left": remaining,
            "files": files,
            "inside": inside,
        }

    def _own_leftovers(self):
        """Files of this interview that the program saved itself."""

        folder = self._study_folder or (
            Path(self.selected_media).parent if self.selected_media else None
        )
        bases = [Path(path).stem for path in (AUDIO_STATE.get("paths") or [])]
        if self.selected_media:
            bases.append(Path(self.selected_media).stem)
        return unanonymised_files(folder, dict.fromkeys(bases))

    def cleanup_run(self):
        """Removes the non-anonymised text. Leaves the audio file alone."""

        with self._project_lock:
            store = self._project_store(create=False)
            if store is None or not self._interview_id:
                return {"ok": False, "error": t("api.interview_not_open")}
            try:
                state = store.anonymization_state(self._interview_id)
                if not state["done"]:
                    return {"ok": False, "error": state["reason"]}
                if not store.anonymized_turns(self._interview_id):
                    return {
                        "ok": False,
                        "error": t("api.cannot_forget_original"),
                    }
                removed = store.forget_original_text(self._interview_id)
            except Exception as error:
                return {"ok": False, "error": str(error)[:400]}

        deleted = []
        for path in self._own_leftovers():
            try:
                path.unlink()
                deleted.append(path.name)
            except OSError:
                continue
        return {"ok": True, "removed": deleted, "inside": removed}

    def project_open(self, path=""):
        """Opens the work on an interview whose recording no longer exists.

        The team has no intention of keeping a corpus of audio: a recording
        is deleted as soon as the interview has been processed. The
        transcript, the proofreading decisions and the anonymisation key
        survive the deletion, and the person must be able to get to them
        without the audio file — otherwise there is no point in keeping them."""

        target = Path(str(path or "")).expanduser()
        gate = self._study_gate()
        if gate is not None:
            return gate
        with self._project_lock:
            if not target.is_file():
                return {"ok": False, "error": t("api.work_file_not_found")}
            # The key belongs to the study: a work file from elsewhere won't
            # open with it, and would be saved outside the study.
            if target.parent.resolve() != Path(self._study_folder).resolve():
                return {"ok": False, "error": t("api.work_outside_study")}
            if self.transcriber.status()["status"] in {"running", "cancelling"}:
                return {"ok": False, "error": t("api.stop_transcription_first")}
            self.transcriber.reset()
            self._close_project()
            self.selected_media = None
            self.media_parts = []
            set_current_audio(None)
            try:
                self._project = ProjectStore(target, self._study_secret)
                self._project.connect()
                state = self._project.load()
            except Exception as error:
                self._close_project()
                return {"ok": False, "error": str(error)[:400]}
            self._project_pinned = True
            if state:
                self._interview_id = state["interview_id"]
            return {"ok": True, "state": state, "path": str(target), "audio": False}

    def bind_window(self, window):
        self.window = window

    def get_ui_language(self):
        """The interface language, and the choices, for the language switch."""

        return {"ok": True, "language": i18n.get_language(), "languages": list(i18n.LANGUAGES)}

    def set_ui_language(self, code):
        """Switches the interface language and remembers it.

        Only the interface: the recording language, the models and the
        pseudonyms written into transcripts do not depend on it."""

        try:
            return {"ok": True, "language": i18n.set_language(code)}
        except ValueError as error:
            return {"ok": False, "error": str(error)[:400]}

    def mark_interface_ready(self):
        with self._ready_lock:
            if self._interface_ready:
                return {"ok": True}
            self._interface_ready = True
        try:
            if self.window is not None:
                self.window.show()
            if self._ready_callback is not None:
                self._ready_callback()
            return {"ok": True}
        except Exception as error:
            return {"ok": False, "error": str(error)[:400]}

    def choose_media(self, path="", parts=None):
        """Chooses a recording. With a path — directly; without one — via a dialog.

        The path comes from the series list: hunting for the file in a dialog
        when it is already shown on screen is needless work."""

        gate = self._study_gate()
        if gate is not None:
            return gate
        if self.transcriber.status()["status"] in {"running", "cancelling"}:
            return {"ok": False, "error": t("api.stop_current_transcription_first")}
        if path:
            try:
                chosen = validate_media_path(path)
                file_parts = [validate_media_path(item) for item in (parts or [])]
            except TranscriptionError as error:
                return {"ok": False, "error": str(error)}
            return self._use_media(chosen, file_parts or self._siblings(chosen))

        if self.window is None:
            return {"ok": False, "error": t("api.window_not_ready")}
        try:
            import webview

            selected = self.window.create_file_dialog(
                webview.FileDialog.OPEN,
                directory=str(self._study_folder),
                allow_multiple=False,
                file_types=(
                    t("api.filetype_media"),
                    t("api.filetype_all"),
                ),
            )
            if not selected:
                return {"ok": True, "cancelled": True}
            chosen = validate_media_path(self._chosen_path(selected))
        except TranscriptionError as error:
            return {"ok": False, "error": str(error)}
        except Exception as error:
            return {"ok": False, "error": str(error)[:400]}
        return self._use_media(chosen, self._siblings(chosen))

    def _siblings(self, path):
        """Other parts of the same interview lying alongside.

        The person picks a file, not a recording: they click «Татьяна1»
        ("Tatiana1") meaning the whole conversation. The program finds the
        second part itself — that is exactly why the file name contains a
        number."""

        neighbours = [
            item
            for item in sorted(Path(path).parent.glob("*"))
            if item.suffix.lower() in MEDIA_SUFFIXES
        ]
        key = parse_interview_name(path).key
        for group in group_parts(neighbours):
            if parse_interview_name(group[0]).key == key:
                return group
        return [Path(path)]

    def _use_media(self, path, parts=None):
        gate = self._study_gate()
        if gate is not None:
            return gate
        self.transcriber.reset()
        with self._project_lock:
            self._close_project()
        # The parts of an interrupted interview are all taken at once: it is
        # one conversation, and transcribing it in halves means getting two
        # different «Спикеров 1» ("Speaker 1"s) and turn numbering starting
        # over.
        paths = [Path(item) for item in (parts or [path])]
        if path not in paths:
            paths.insert(0, path)
        self.selected_media = paths[0]
        self.media_parts = paths
        file_parts = audio_parts(paths)
        parsed = parse_interview_name(paths[0])
        return {
            "ok": True,
            "cancelled": False,
            "name": paths[0].name,
            "label": parsed.label,
            "interviewer": parsed.interviewer,
            "respondent": parsed.respondent,
            "path": str(paths[0]),
            "size": sum(item.stat().st_size for item in paths),
            "extension": paths[0].suffix.lower()[1:].upper(),
            "duration": sum(item["duration"] for item in file_parts),
            "audio_token": set_current_audio(paths),
            "parts": file_parts,
            "project": str(project_path_for(paths[0], self._project_home())),
        }

    def start_transcription(self, speaker_count=2, guide=""):
        gate = self._study_gate()
        if gate is not None:
            return gate
        if self.selected_media is None:
            return {"ok": False, "error": t("api.choose_media_first")}
        file_parts = self.media_parts or [self.selected_media]
        try:
            requested_speakers = validate_speaker_count(speaker_count)
        except TranscriptionError as error:
            return {"ok": False, "error": str(error)}
        return self.transcriber.start(
            file_parts,
            requested_speakers,
            str(guide or ""),
        )

    def get_transcription_status(self):
        return self.transcriber.status()

    def cancel_transcription(self):
        return self.transcriber.cancel()

    def reset_transcription(self):
        result = self.transcriber.reset()
        if result.get("ok"):
            self.selected_media = None
            set_current_audio(None)
            with self._project_lock:
                self._close_project()
        return result

    @staticmethod
    def _chosen_paths(selected):
        """Paths from the system file-picker window.

        pywebview answers in different ways: the save dialog returns a single
        string, the file or folder picker returns a sequence. A string is
        easily mistaken for a sequence, and then the first element turns out
        to be the first character of the path. On the Mac that is "/", and
        the program tries to write to the root of the disk."""

        if not selected:
            return []
        if isinstance(selected, (str, bytes, os.PathLike)):
            return [Path(os.fsdecode(selected))]
        return [Path(os.fsdecode(path)) for path in selected]

    @classmethod
    def _chosen_path(cls, selected):
        paths = cls._chosen_paths(selected)
        return paths[0] if paths else None

    @staticmethod
    def _safe_text_name(suggested_name):
        safe_name = re.sub(
            r'[<>:"/\\|?*\x00-\x1f]',
            "_",
            Path(str(suggested_name)).name,
        )
        safe_name = safe_name.strip(" .") or "расшифровка.txt"
        if not safe_name.lower().endswith(".txt"):
            safe_name += ".txt"
        return safe_name

    def save_text(self, suggested_name, content):
        if self.window is None:
            return {"ok": False, "error": t("api.window_not_ready")}
        safe_name = self._safe_text_name(suggested_name)

        try:
            import webview

            selected = self.window.create_file_dialog(
                webview.FileDialog.SAVE,
                save_filename=safe_name,
                file_types=(t("api.filetype_text"), t("api.filetype_all")),
            )
            if not selected:
                return {"ok": True, "cancelled": True}
            destination = self._chosen_path(selected)
            destination.write_text(str(content), encoding="utf-8")
            return {
                "ok": True,
                "cancelled": False,
                "name": destination.name,
                "path": str(destination),
            }
        except Exception as error:
            return {"ok": False, "error": str(error)[:400]}

    def save_docx(self, suggested_name, content, title=""):
        """Saves the finished transcript as a document.

        Built with the standard library: a .docx is a zip with XML, and it is
        not worth pulling in yet another package for it."""

        if self.window is None:
            return {"ok": False, "error": t("api.window_not_ready")}
        safe_name = re.sub(
            r'[<>:"/\\|?*\x00-\x1f]', "_", Path(str(suggested_name)).name
        ).strip(" .") or "расшифровка"
        if not safe_name.lower().endswith(".docx"):
            safe_name = re.sub(r"\.txt$", "", safe_name, flags=re.IGNORECASE) + ".docx"

        try:
            import webview

            from alumenans.docx_export import save_transcript

            selected = self.window.create_file_dialog(
                webview.FileDialog.SAVE,
                save_filename=safe_name,
                file_types=(t("api.filetype_docx"), t("api.filetype_all")),
            )
            if not selected:
                return {"ok": True, "cancelled": True}
            destination = self._chosen_path(selected)
            if destination.suffix.lower() != ".docx":
                destination = destination.with_suffix(".docx")
            save_transcript(destination, str(content), str(title or ""))
            return {
                "ok": True,
                "cancelled": False,
                "name": destination.name,
                "path": str(destination),
            }
        except Exception as error:
            return {"ok": False, "error": str(error)[:400]}

    def save_key_archive(self, suggested_name, content, password, entries=0):
        """The anonymisation key exists only under a password.

        The archive is an ordinary zip: 7-Zip, WinRAR or the program itself
        will open it. The very same encrypted copy goes into the project
        file — not as a backup, but because project files are passed from
        hand to hand, and an unprotected key has no place in them. The
        password is stored nowhere: forget it, and the key cannot be opened."""

        if self.window is None:
            return {"ok": False, "error": t("api.window_not_ready")}
        if not str(password or ""):
            return {"ok": False, "error": t("api.key_needs_password")}

        safe_name = re.sub(
            r'[<>:"/\\|?*\x00-\x1f]', "_", Path(str(suggested_name)).name
        ).strip(" .") or "КЛЮЧ"
        inner_name = safe_name if safe_name.lower().endswith(".txt") else safe_name + ".txt"
        archive_name = re.sub(r"\.txt$", "", inner_name, flags=re.IGNORECASE) + ".zip"

        try:
            import webview

            from alumenans.storage.secret_zip import make_secret_zip

            selected = self.window.create_file_dialog(
                webview.FileDialog.SAVE,
                save_filename=archive_name,
                file_types=(t("api.filetype_zip"), t("api.filetype_all")),
            )
            if not selected:
                return {"ok": True, "cancelled": True}
            destination = self._chosen_path(selected)
            if destination.suffix.lower() != ".zip":
                destination = destination.with_suffix(".zip")
            archive = make_secret_zip(inner_name, str(content), str(password))
            destination.write_bytes(archive)
            self._keep_key_archive(archive, entries)
            return {
                "ok": True,
                "cancelled": False,
                "name": destination.name,
                "path": str(destination),
            }
        except Exception as error:
            return {"ok": False, "error": str(error)[:400]}

    def open_new_words_email(self, words):
        if self.selected_media is None:
            return {
                "ok": False,
                "error": t("api.no_audio"),
            }
        cleaned = []
        seen = set()
        for value in words if isinstance(words, list) else []:
            word = re.sub(r"\s+", " ", str(value)).strip()
            key = word.casefold()
            if not word or key in seen:
                continue
            seen.add(key)
            cleaned.append(word[:160])
        if not cleaned:
            return {"ok": False, "error": t("api.no_new_words")}

        destination = Path(self.selected_media).parent / "Новые слова.txt"
        try:
            destination.write_text(
                "\n".join(sorted(cleaned, key=str.casefold)) + "\n",
                encoding="utf-8",
            )
            _schedule_new_words_email(destination, cleaned)
            return {"ok": True, "path": str(destination)}
        except Exception as error:
            return {"ok": False, "error": str(error)[:400]}
