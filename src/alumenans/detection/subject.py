"""The subject of the study: terms the search must leave alone."""

from alumenans.storage.study_store import matches_subject_term

# ---------------------------------------------------------------------------
# The subject of the study.
#
# The study is about some group; if the anonymiser replaces the group with
# «Организация 1» ("Organisation 1"), the transcript becomes useless. The
# models don't know this — to them it is an organisation no different from
# the respondent's place of work.
#
# The list of confirmed terms comes from the study file and lives here,
# because the search for findings runs outside the window. An empty list is
# the normal state: until the person has confirmed anything, nothing is
# protected.
# ---------------------------------------------------------------------------

SUBJECT_STATE = {"terms": ()}


def set_subject_terms(terms):
    """Remembers the confirmed terms for the subject of the study."""

    cleaned = tuple(
        str(term).strip()
        for term in (terms or ())
        if str(term or "").strip()
    )
    SUBJECT_STATE["terms"] = cleaned
    return cleaned


def is_subject_fragment(fragment):
    return any(
        matches_subject_term(fragment, term) for term in SUBJECT_STATE["terms"]
    )
