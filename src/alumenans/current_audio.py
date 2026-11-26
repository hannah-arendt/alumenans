"""The recording that is open now: which files it is and what their name says."""

import time
from pathlib import Path

from alumenans.interview_name import parse_interview_name
from alumenans.transcription.core import probe_media_duration

# ---------------------------------------------------------------------------
# Audio for proofreading.
#
# In the proofreading tab the person is asked to correct a word they cannot
# hear. Yet we already have both the file and the timestamps of every word.
# So the server serves the chosen recording with support for Range requests:
# without them WebView downloads the whole file and cannot seek.
#
# None of this goes outside, as before: the server listens only on 127.0.0.1
# and serves exactly one file — the one the person chose themselves in this
# window.
# ---------------------------------------------------------------------------

# An interrupted interview lies on disk as several files. Each is served to
# the player separately, while proofreading knows them as one continuous
# sound: the time of the second part continues the first.
AUDIO_STATE = {"paths": [], "token": ""}


def set_current_audio(path):
    """Remembers the recording that can be listened to in the proofreading window."""

    if path is None:
        AUDIO_STATE["paths"] = []
        AUDIO_STATE["token"] = ""
        return ""
    paths = [path] if isinstance(path, (str, Path)) else list(path)
    AUDIO_STATE["paths"] = [Path(item) for item in paths]
    AUDIO_STATE["token"] = f"{int(time.time() * 1000):x}"
    return AUDIO_STATE["token"]


def audio_parts(paths):
    """The parts of the recording with their start in the overall interview time."""

    file_parts = []
    start = 0.0
    for index, path in enumerate(paths):
        length = probe_media_duration(path) or 0.0
        file_parts.append(
            {
                "index": index,
                "name": Path(path).name,
                "start": round(start, 3),
                "duration": round(length, 3),
            }
        )
        start += length
    return file_parts


def current_pseudonym():
    """The respondent's pseudonym — the one in the audio file's name.
    The trailing digit belongs to the recording number, not the name, and is not included here."""

    path = AUDIO_STATE.get("paths") or []
    if not path:
        return ""
    parsed_name = parse_interview_name(path[0])
    return parsed_name.respondent if parsed_name.recognised else ""
