"""The search for personal data: every source of findings, merged and grouped."""

from alumenans.detection.curated import dict_spans, is_public_name
from alumenans.detection.filters import keep_entity_span
from alumenans.detection.models import MODEL_STATE, gliner_model, tagger
from alumenans.detection.morphology import grammar_of, lemma_of, morph_tools
from alumenans.echoes import echo_spans
from alumenans.introduction import self_introduction_spans
from alumenans.places import place_kind
from alumenans.speech_noise import restore_span, smooth
from alumenans.vocative import morphology_checks as vocative_checks, vocative_spans

TYPE_MAP = {"PER": "person", "ORG": "org", "LOC": "place"}


GLINER_LABELS = {
    "имя человека": "person",
    "фамилия": "person",
    "название организации": "org",
    "учебное заведение": "org",
    "город или населённый пункт": "place",
    "адрес": "place",
    "возраст": "age",
    "дата рождения": "birth",
}


def gliner_spans(text):
    """GLiNER findings go into the doubtful layer: the model grabs too much,
    but it sees what is in neither the dictionary nor Natasha."""
    model = gliner_model()
    labels = list(GLINER_LABELS)
    out = []
    # Cut into pieces: the model can't digest a long text in one go.
    step = 1200
    for start in range(0, len(text), step):
        piece = text[start:start + step]
        for found in model.predict_entities(piece, labels, threshold=0.5):
            cat = GLINER_LABELS.get(found["label"])
            if not cat:
                continue
            out.append({"start": start + found["start"],
                        "stop": start + found["end"],
                        "cat": cat, "conf": "weak", "by": "gliner"})
    return out


def natasha_spans(text, by="natasha"):
    """What the sentence parser considers a name, a title, a place."""

    from natasha import Doc

    segmenter, ner = tagger()
    doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_ner(ner)

    found = []
    for span in doc.spans:
        category = TYPE_MAP.get(span.type)
        if not category:
            continue
        if len(text[span.start:span.stop].strip()) < 2:
            continue
        found.append(
            {
                "start": span.start,
                "stop": span.stop,
                "cat": category,
                "conf": "strong",
                "by": by,
            }
        )
    return found


def false_start_spans(text):
    """Names lost to a slip of the tongue — a second pass over a clean copy.

    «Работать на с <имя>» ("to work on with <name>") — the person started
    «над» ("on"), changed their mind and said «с» ("with"). The parser loses
    the name on this, even though in the intact phrase it finds it
    confidently. The clean copy exists only for the sake of this parse; the
    transcript text does not change."""

    clean_text, offsets = smooth(text)
    if clean_text == text:
        return []
    restored = []
    for span in natasha_spans(clean_text, by="false-start"):
        piece = clean_text[span["start"]:span["stop"]]
        place = restore_span(span["start"], span["stop"], offsets)
        if place is None:
            continue
        # The finding may have taken in the spot the preposition was dropped
        # from. Then in the original text something else sits at these
        # boundaries, and it must not be taken: the wrong thing would be
        # replaced.
        if text[place[0]:place[1]] != piece:
            continue
        span["start"], span["stop"] = place
        restored.append(span)
    return restored


def find_spans(text):
    spans = [
        span for span in natasha_spans(text) if keep_entity_span(text, span)
    ]
    spans.extend(false_start_spans(text))
    spans.extend(dict_spans(text))
    spans.extend(vocative_name_spans(text))
    if MODEL_STATE.get("gliner") == "ready":
        try:
            spans.extend(gliner_spans(text))
        except Exception:
            MODEL_STATE["gliner"] = "off"
    spans = [span for span in spans if keep_entity_span(text, span)]
    # The parser does not recognise the same word in every sentence. A
    # repeat of a name that has already been recognised is personal data
    # too.
    spans.extend(
        span for span in echo_spans(text, spans) if keep_entity_span(text, span)
    )

    # Remove overlaps: a long finding matters more than a short one.
    spans.sort(key=lambda s: (s.get("conf") == "weak", s["start"] - s["stop"], s["start"]))
    kept = []
    for span in spans:
        if any(span["start"] < k["stop"] and span["stop"] > k["start"] for k in kept):
            continue
        kept.append(span)
    kept.sort(key=lambda s: s["start"])
    # Publicness is set after selection: overlapping findings are weeded
    # out, and the dictionary one may not have survived to this point.
    for span in kept:
        if not span.get("public"):
            span["public"] = is_public_name(text[span["start"]:span["stop"]])
    # What kind of place this is: country, region, town, address. Otherwise
    # the anonymiser suggests «Город 1» ("Town 1") both for Georgia and for
    # a street.
    for span in kept:
        if span.get("cat") == "place":
            span["place_kind"] = place_kind(
                text[span["start"]:span["stop"]],
                before=text[max(0, span["start"] - 40):span["start"]],
                lemma=lemma_of,
            )
    # The name the respondent introduced themselves by: its replacement is
    # known in advance — it is in the file name.
    try:
        introductions = self_introduction_spans(text)
    except Exception:
        introductions = []
    for span in kept:
        span["self"] = any(
            span["start"] < end and span["stop"] > start
            for start, end in introductions
        )
    try:
        grammar_of(text, kept)
    except Exception:
        pass
    return kept


def vocative_name_spans(text):
    """Names used in address: «Миш», «Саш», «Кать» (truncated vocatives).

    The name parser knows «Мишу» (accusative of «Миша»), but not «Мишь». In
    interviews this is exactly how people call each other, and these names
    were remaining in the text after anonymisation."""

    try:
        _, pymorphy = morph_tools()
        checks = vocative_checks(pymorphy)
        return vocative_spans(
            text,
            is_vocative_form=checks[0],
            is_personal_name=checks[1],
        )
    except Exception:
        # The morphology dictionary is not required for the rest of the parse.
        return []


def entity_groups(text, spans):
    """Groups findings into entities: one person — one pseudonym.

    Each finding gets an entity number; everything else — which pseudonym
    to give it, and whether to give one at all — is still up to the person."""

    try:
        from alumenans.entities import group_mentions
    except Exception:
        return []
    # A mention's key is its place in the text: the interface discards
    # overlapping findings and numbers the remaining ones its own way.
    mentions = [
        {
            "id": f"{span['start']}:{span['stop']}",
            "cat": span.get("cat"),
            "text": text[span["start"]:span["stop"]],
            "public": bool(span.get("public")),
            "self": bool(span.get("self")),
            "place_kind": span.get("place_kind") or "",
        }
        for span in spans
    ]
    try:
        entities = group_mentions(mentions, lemma=lemma_of)
    except Exception:
        return []
    public = {
        mention["id"] for mention in mentions if mention["public"]
    }
    introduced = {
        mention["id"] for mention in mentions if mention["self"]
    }
    place_kinds = {
        mention["id"]: mention["place_kind"]
        for mention in mentions
        if mention["place_kind"]
    }
    ready_items = []
    for entity in entities:
        entry = entity.as_dict()
        # One public mention is enough: if «Нужна помощь» was recognised by
        # the dictionary even once, the whole foundation is public.
        entry["public"] = any(place in public for place in entity.mentions)
        # The respondent names themselves once, but later they are referred
        # to by surname and in other cases: the whole entity needs the mark.
        entry["self"] = any(place in introduced for place in entity.mentions)
        kinds = [place_kinds[place] for place in entity.mentions if place_kinds.get(place)]
        entry["place_kind"] = kinds[0] if kinds else ""
        ready_items.append(entry)
    return ready_items
