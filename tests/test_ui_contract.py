#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from alumenans.i18n import screen_catalogs
from alumenans.paths import PACKAGE_DIR

# Interface text lives in the screen's catalog; the markup and JS carry keys.
RU = screen_catalogs()["ru"]


HTML = PACKAGE_DIR.joinpath("resources", "anonymizer.html").read_text(
    encoding="utf-8"
)
# The page's wording lives in its catalog script; checks of wording look at both.
UI = HTML + PACKAGE_DIR.joinpath("resources", "i18n.js").read_text(encoding="utf-8")


class WorkflowContractTests(unittest.TestCase):
    def test_both_anonymization_entries_use_reviewed_text_flow(self):
        self.assertIn('$("tabAnonymizer").onclick = openAnonymizer;', HTML)
        self.assertIn('$("btnProofToAnonymizer").onclick = openAnonymizer;', HTML)
        # keepKey — so that returning to proofreading doesn't wipe out the replacements
        # already made together with their key.
        self.assertIn(
            'load(mapped.text, proofFileName(PROOF.sourceName), {focusFirst:false, keepKey:true});',
            HTML,
        )

    def test_anonymizer_warmup_starts_from_proofreading(self):
        self.assertIn(
            "if (proofreading || anonymizer) ensureAnonymizerReady();",
            HTML,
        )

    def test_main_window_reports_that_python_bridge_is_ready(self):
        self.assertIn(
            "await window.pywebview.api.mark_interface_ready();",
            HTML,
        )

    def test_intermediate_file_names_and_labels_are_explicit(self):
        self.assertIn("Транскрибировать другое аудио", UI)
        # The raw transcript isn't saved anywhere but the encrypted interview file.
        self.assertNotIn('id="btnSaveTranscript"', HTML)
        self.assertIn(
            "${base}-расшифровка-вычитана-неаномизирована.txt",
            HTML,
        )
        self.assertNotIn("<h1>Вычитка</h1>", HTML)
        self.assertNotIn(">После транскрибации<", HTML)

    def test_proofreading_has_guide_suggestions_and_new_word_flow(self):
        self.assertIn('id="interviewGuide"', HTML)
        self.assertIn('data-proof-action="remember"', HTML)
        self.assertIn('appCall("open_new_words_email"', HTML)
        self.assertNotIn("proofFixCount", HTML)
        self.assertNotIn("textDirty", HTML)

    def test_proofreading_can_play_the_recording(self):
        self.assertIn('id="proofAudio"', HTML)
        self.assertIn('id="proofPlayer"', HTML)
        # The audio is fetched from the server, not from the file system directly.
        self.assertIn(
            "`/audio?v=${encodeURIComponent(PLAYER.token)}&part=${index}&session=${encodeURIComponent(SESSION)}`", HTML
        )
        # A word carries both its own time and the boundaries of its phrase.
        self.assertIn('data-ps="${phrase.start.toFixed(2)}"', HTML)
        self.assertIn('data-proof-action="listen"', HTML)

    def test_the_player_spans_all_parts_of_one_interview(self):
        # An interrupted interview lies in several files, but the person has one
        # timeline: they move the slider further, and the player switches files itself.
        self.assertIn("function seekPlayer(seconds, autoplay)", HTML)
        self.assertIn("function playerTime()", HTML)
        self.assertIn("function playerDuration()", HTML)
        self.assertIn("const index = wordIndexAt(playerTime());", HTML)
        # A part has ended — play the next one rather than stop.
        self.assertIn('proofAudio().addEventListener("ended"', HTML)

    def test_header_and_player_are_pinned_together(self):
        sticky = HTML.split('<div class="proof-sticky">')[1].split(
            '<div class="proof-turns"'
        )[0]
        self.assertIn('id="proofMeta"', sticky)
        self.assertIn('id="proofStatus"', sticky)
        self.assertIn('id="proofPlayer"', sticky)
        self.assertIn(".proof-sticky{\n  position:sticky; top:0;", HTML)
        # There must be no divider lines inside the shade.
        head = HTML.split(".proof-head{")[1].split("}")[0]
        player = HTML.split(".proof-player{")[1].split("}")[0]
        self.assertNotIn("border-bottom", head)
        self.assertNotIn("border-bottom", player)

    def test_player_stays_a_single_thin_row(self):
        self.assertIn('$("btnProofRepeat").onclick = repeatLastRange;', HTML)
        # Everything that moved to the turns and to the right column is gone from the player.
        for removed in (
            'id="proofPlayerScope"',
            'id="proofPlayerFollow"',
            'data-rate="0.75"',
            "Идти за звуком",
        ):
            self.assertNotIn(removed, HTML)

    def test_sound_is_started_only_by_buttons(self):
        # The word is highlighted but doesn't start the audio by itself.
        self.assertIn('data-listen-turn="${index}"', HTML)
        self.assertIn('data-proof-action="listen"', HTML)
        self.assertNotIn("playAtWord", HTML)

    def test_issue_actions_are_named_plainly(self):
        self.assertIn('data-proof-action="ignore"\n                data-proof-id="${escAttr(issue.id)}">${tr("anon.keep")}<', HTML)
        self.assertIn('data-proof-action="replace"\n                data-proof-id="${escAttr(issue.id)}">${tr("proof.replace")}<', HTML)
        self.assertEqual(RU["anon.keep"], "Оставить")
        self.assertEqual(RU["proof.replace"], "Заменить")
        self.assertNotIn('data-proof-action="show"', HTML)
        self.assertNotIn("Показать в тексте", HTML)
        self.assertNotIn('id="btnBackToTranscript"', HTML)

    def test_the_anonymized_text_is_saved_only_when_nothing_is_pending(self):
        # An undecided finding stays as the original words in buildClean().
        self.assertIn(
            "button.disabled = left > 0 || (!state.ents.length && !state.speakerRoles.size);",
            HTML,
        )
        self.assertIn("const left = pendingEntities();", HTML)
        handler = HTML.split('$("btnSaveTxt").onclick = () => {')[1].split("};")[0]
        self.assertLess(
            handler.index("if (pendingEntities() > 0) return;"),
            handler.index("save(anonymisedName()"),
        )
        # No other place may enable the button on its own terms.
        self.assertEqual(HTML.count('$("btnSaveTxt").disabled = '), 2)

    def test_proofreading_leads_only_to_anonymization(self):
        # No button saves the proofread text: it is not anonymized yet, and
        # the work already lives in the encrypted interview file.
        rail = HTML.split('<div class="proof-rail-foot">')[1].split("</div>")[0]
        self.assertIn('data-i18n="proof.to_anonymizer"', rail)
        self.assertNotIn('id="btnSaveProof"', HTML)
        self.assertNotIn("proof.save", rail)
        self.assertEqual(RU["proof.to_anonymizer"], "Анонимизировать")

    def test_timecode_button_does_not_move_the_text(self):
        self.assertIn("keepingScroll(renderProof);", HTML)

    def test_new_issue_is_centred_on_screen(self):
        self.assertIn("function centerInProof(node)", HTML)
        self.assertIn("if (next) focusProofIssue(next);", HTML)

    def test_batch_replacement_is_guarded(self):
        self.assertIn('data-proof-action="everywhere"', HTML)
        self.assertIn("function collectSimilarForms(fragment)", HTML)
        # Two safeguards against replacing an unrelated word: letter case and the dictionary.
        self.assertIn("const sameCase = !named || startsCapital(piece);", HTML)
        self.assertIn('localFetch("/known-words"', HTML)
        self.assertIn("есть в словаре русского языка", UI)

    def test_word_can_be_deleted_not_only_replaced(self):
        self.assertIn('data-proof-action="delete"', HTML)
        self.assertIn('replaceProofIssue(issue, "");', HTML)
        # An empty replacement is a seam, not just cut-out characters.
        self.assertIn("function healSeam(text, at)", HTML)
        self.assertIn("toLocaleUpperCase(\"ru\")", HTML)
        # We listen to the whole turn; its boundaries don't change with an edit,
        # so the word timings must be edited, not thrown away.
        self.assertIn("function reflowTurnWords(turn, start, end, replacement)", HTML)
        # A free edit can no longer break the link: it is simply
        # not accepted if the number of words changed.
        self.assertEqual(HTML.count("turn.words = [];"), 0)

    def test_manual_edit_keeps_the_link_between_letters_and_sound(self):
        # An edit via selection goes the same way as the program's replacements,
        # so the new piece gets the old one's time.
        self.assertIn("function selectionInsideTurn()", HTML)
        self.assertIn("function applyManualEdit(replacement)", HTML)
        self.assertIn("reflowTurnWords(turn, start, end, replacement);", HTML)
        self.assertIn('addEventListener("contextmenu"', HTML)
        self.assertIn('data-manual-action="apply"', HTML)
        # Button order: the ordinary edit first, changing the speaker last.
        self.assertLess(
            HTML.index('data-manual-action="apply"'),
            HTML.index('data-manual-action="speaker"'),
        )

    def test_free_editing_cannot_change_the_number_of_words(self):
        # Otherwise the text's words and the recording's words drift apart, and the turn
        # stops playing. The edit is rejected rather than silently accepted.
        self.assertIn(
            "if (countWords(text) !== countWords(turn.text)){\n"
            "    element.textContent = turn.text;",
            HTML,
        )
        self.assertIn('id="proofEditHint"', HTML)
        self.assertIn("кликните по нему правой кнопкой мыши", UI)
        self.assertEqual(HTML.count("turn.words = [];"), 0)

    def test_speaker_attribution_is_fixed_through_the_selection(self):
        # There are no separate controls: both splitting and changing the speaker
        # are done by selecting a piece and right-clicking.
        self.assertIn("function moveSelectionToSpeaker(spot, speaker)", HTML)
        self.assertIn('data-manual-action="speaker"', HTML)
        self.assertIn("Реплика другого спикера", UI)
        self.assertNotIn("data-speaker-for", HTML)
        self.assertNotIn("data-merge-turn", HTML)

    def test_two_turns_of_one_speaker_become_one(self):
        # A merge button isn't needed: merging happens by itself once the reason
        # to keep them apart is gone.
        self.assertIn("function normalizeTurns()", HTML)
        self.assertNotIn("function mergeWithPrevious", HTML)

    def test_speaker_count_is_fixed_at_transcription(self):
        self.assertIn("function speakerCount()", HTML)
        self.assertIn("requested_speakers", HTML)
        # With two participants there is nothing to choose from.
        self.assertIn("function otherSpeaker(current)", HTML)

    def test_editing_can_be_undone(self):
        self.assertIn("const HISTORY = {past:[], future:[], limit:10};", HTML)
        self.assertIn("function undoProof()", HTML)
        self.assertIn("function redoProof()", HTML)
        # The snapshot is taken before the change, not after.
        self.assertGreaterEqual(HTML.count("remember();"), 6)

    def test_hint_sits_under_the_player_and_goes_away(self):
        # The exact wording of the hint is checked separately,
        # in test_proof_hint_and_undo.py.
        self.assertIn('id="proofEditHint"', HTML)
        self.assertIn("В этом окне можно вносить только мелкие правки", UI)
        self.assertIn("function dismissEditHint()", HTML)
        self.assertIn("setTimeout(dismissEditHint, 60000)", HTML)
        hint = HTML.split(".proof-edit-hint{")[1].split("}")[0]
        self.assertIn("font:16.5px/1.6 var(--serif)", hint)

    def test_split_and_merge_keep_the_words_of_the_recording(self):
        # A turn is split together with its words, otherwise both halves
        # would lose their link to the audio.
        split = HTML.split("function splitTurn(index, offset)")[1].split(
            "function mergeWithPrevious"
        )[0]
        self.assertIn("const flat = expandTurnWords(turn);", split)
        self.assertIn("words: after", split)
        self.assertIn("turn.words = before;", split)
        merge = HTML.split("function normalizeTurns()")[1].split("\n}")[0]
        self.assertIn(
            "previous.words = [...expandTurnWords(previous), ...expandTurnWords(turn)];",
            merge,
        )
        # After the number of turns changes, the old remarks point to the wrong places.
        self.assertIn("void runProofreading();", HTML)

    def test_two_hyphens_become_a_dash_while_typing(self):
        self.assertIn("function fixDashes(text)", HTML)
        self.assertIn('replace(/(^|[^-])--(?!-)/g, "$1—")', HTML)
        # The second hyphen is intercepted before it gets into the text, otherwise
        # we would have to fix what's written and put the cursor back again.
        self.assertIn('addEventListener("beforeinput"', HTML)
        self.assertIn('node.insertData(at - 1, "—");', HTML)

    def test_guide_and_notes_live_only_on_the_first_tab(self):
        self.assertNotIn('id="proofGuide"', HTML)
        self.assertNotIn('id="proofNotes"', HTML)
        rail = HTML.split('<div class="proof-rail-scroll">')[1].split("</aside>")[0]
        self.assertIn('data-i18n="proof.coverage"', rail)
        self.assertIn('data-i18n="proof.topics"', rail)
        self.assertNotIn('data-i18n="notes.label"', rail)
        self.assertEqual(RU["proof.coverage"], "Покрытие гайда")
        self.assertEqual(RU["proof.topics"], "Таймкоды тем")

    def test_first_screen_has_no_decorative_rules(self):
        media = HTML.split(".media-section{")[1].split("}")[0]
        self.assertNotIn("border-top", media)
        self.assertNotIn("border-bottom", media)
        # Guide, programme and notes stand in one row, not one below another.
        self.assertIn('<div class="interview-context-row">', HTML)
        self.assertIn("grid-template-columns:repeat(3, minmax(0, 1fr))", HTML)

    def test_guide_coverage_is_shown_in_the_rail(self):
        self.assertIn('id="proofCoveragePanel"', HTML)
        self.assertIn('localFetch("/guide-report"', HTML)
        # The report is recomputed after every text check.
        self.assertIn("void runCoverage();", HTML)
        # The states of findings are distinguishable, not lumped into "yes / no".
        for state in ("вероятно", "вместе с соседним", "не по порядку"):
            self.assertIn(state, HTML)
        # A question that wasn't found leads nowhere.
        self.assertIn('${clickable ? "" : "flat"}', HTML)

    def test_every_request_to_the_local_server_carries_the_session(self):
        # The server refuses anything without this launch's secret.
        self.assertIn('const SESSION = new URLSearchParams(location.search).get("session") || "";', HTML)
        self.assertIn('headers.set("X-ALumenans-Session", SESSION);', HTML)
        self.assertNotRegex(HTML, r'(?<!local)fetch\("/')

    def test_enter_in_a_password_dialog_confirms(self):
        # In a dialog form Enter presses the first submit button: Cancel must
        # not be one, or Enter would throw away the typed password.
        for cancel, dialog in (("passCancel", "keyPassword"), ("studyPassCancel", "studyPassword")):
            with self.subTest(dialog=dialog):
                self.assertIn(f'<button type="button" value="cancel" id="{cancel}"', HTML)
                self.assertIn(f'$("{cancel}").onclick = () => $("{dialog}").close("cancel");', HTML)

    def test_the_study_opens_only_with_its_password(self):
        # One password for every file of the study: set when it's started,
        # asked once per session to open it.
        self.assertIn('id="studyPassword"', HTML)
        self.assertIn("function askStudyPassword(mode, errorText)", HTML)
        self.assertIn('appCall(mode === "new" ? "study_create" : "study_unlock", password)', HTML)
        # A wrong password is asked again, with the reason shown.
        self.assertIn("} else if (reply.wrong_password){", HTML)
        # No recording before the study is open: its work is saved only there.
        self.assertIn(
            '$("btnChooseMedia").disabled = !TRANSCRIBER.bridgeReady || active || !STUDY.unlocked;',
            HTML,
        )

    def test_study_is_chosen_once_and_read_from_the_folder(self):
        self.assertIn('id="btnStudyFolder"', HTML)
        self.assertIn('id="researcherName"', HTML)
        # Without a folder the picker opens; the last study is opened by its path.
        self.assertIn('async function openStudyFolder(folder = ""){', HTML)
        self.assertIn('appCall("study_open", folder)', HTML)
        self.assertIn('$("btnLastStudy").onclick = () => void openStudyFolder(LAST_STUDY.folder);', HTML)
        self.assertIn('void loadLastStudy();', HTML)
        # The file name already contains the series, interviewer, number and pseudonym.
        self.assertIn('id="studyList"', HTML)
        self.assertIn('id="studyProblems"', HTML)
        # The recording is picked from the series list, not through a file-open dialog.
        self.assertIn('appCall("choose_media", recording.paths[0], recording.paths)', HTML)
        # The guide and programme belong to the study.
        self.assertIn('"study_save_documents"', HTML)

    def test_straight_quotes_become_russian_ones(self):
        self.assertIn("function fixQuotes(text)", HTML)
        self.assertIn("fixQuotes(fixDashes(", HTML)

    def test_typing_inside_a_turn_is_undone_by_the_browser(self):
        # Otherwise the program swallows Ctrl+Z and gives nothing in return:
        # it keeps its own history by whole actions, not by letters.
        self.assertIn('closest(".proof-turn-text")', HTML)
        self.assertIn("if (inTextField) return;", HTML)
        self.assertIn("if (isUndo && !HISTORY.past.length) return;", HTML)

    def test_a_turn_has_both_play_and_pause(self):
        self.assertIn('data-listen-turn="${index}"', HTML)
        self.assertIn('data-pause-turn="1"', HTML)
        self.assertIn(".proof-turn-sound{", HTML)

    def test_the_anonymised_file_is_named_afresh(self):
        # The name must keep neither the part number nor any trace of «неанонимизирована» ("not anonymised").
        self.assertIn("function anonymisedName()", HTML)
        self.assertIn('save(anonymisedName() + ".txt", buildClean());', HTML)
        self.assertNotIn("btnSaveText", HTML)
        self.assertIn("Сохранить анонимизированный текст", UI)

    def test_a_public_name_is_left_alone_by_default(self):
        # The names dictionary is a protective list: Greenpeace and «Нужна помощь» ("Help Needed")
        # give nobody away, and a transcript without them is unreadable.
        self.assertIn("if (entity.public) publicIds.add(", HTML)
        self.assertIn('g.status = "kept";', HTML)
        self.assertIn("g.public = true;", HTML)

    def test_finishing_anonymisation_is_recorded(self):
        # Coding non-anonymised text is not allowed, and it is the program, not
        # the person, that has to remember this.
        self.assertIn("function markAnonymizationDone()", HTML)
        self.assertIn(
            'appCall("project_mark_anonymized", pendingEntities(), anonymisedTurns())',
            HTML,
        )
        self.assertIn("function pendingEntities()", HTML)

    def test_one_person_gets_one_pseudonym(self):
        # Entities come from the program: it knows cases and pronunciation and
        # so sees that «Ирина Михайлова» and «Михайлова» are one person.
        self.assertIn("function buildGroups(entities)", HTML)
        self.assertIn("buildGroups(data.entities);", HTML)
        self.assertIn("byMention.get(`${e.start}:${e.end}`)", HTML)
        # Our own crude grouping remains: the html is also opened without the program.
        self.assertIn("groupKey(e)", HTML)

    def test_missing_word_is_written_into_the_gap(self):
        # Recognition sometimes loses a word entirely. A selected space is
        # an edit like any other, and goes the same way.
        self.assertIn("gap: !text.trim()", HTML)
        self.assertIn("Пропущенное слово", UI)
        # The typed-in word goes between its neighbours, not in place of the space.
        self.assertIn("replacement = `${edit.text}${String(replacement).trim()}${edit.text}`;", HTML)
        # And gets its own time: otherwise the highlighting goes off during playback.
        self.assertIn("if (!span && added){", HTML)
        self.assertIn("let span = cut.length", HTML)

    def test_listening_to_a_turn_is_an_icon_not_a_caption(self):
        # The button is on every turn; a label repeated twenty times
        # gets in the way of reading the text itself.
        self.assertIn('aria-label="${tr("proof.listen_turn")}"', HTML)
        self.assertNotIn('>${tr("proof.listen_turn")}</button>', HTML)
        self.assertEqual(RU["proof.listen_turn"], "Слушать реплику")
        self.assertIn('<path d="M2.5 1.5L10 6l-7.5 4.5z"/>', HTML)

    def test_the_recording_is_called_a_file_not_a_sound(self):
        # The audio file and the spoken audio are different things, and they must not
        # be confused in the labels: one gets deleted, the other gets listened to.
        for wrong in ("звука нет", "Готовлю звук"):
            self.assertNotIn(wrong, HTML)
        self.assertIn("аудиофайла нет", UI)
        self.assertIn("Готовлю аудиофайл", UI)

    def test_interview_without_a_recording_stays_openable(self):
        # Audio is a consumable: it is deleted once the interview is processed, and
        # the interview must stay in the series list and open without it.
        self.assertIn("function describeRecording(recording)", HTML)
        self.assertIn('appCall("project_open", recording.project)', HTML)
        self.assertIn("if (!recording.audio){", HTML)
        self.assertIn('"аудиофайла нет"', UI)
        # Work continues from proofreading rather than starting over.
        self.assertIn("if (PROJECT.candidate) resumeSavedProject();", HTML)

    def test_subject_of_the_study_is_confirmed_by_hand(self):
        self.assertIn('id="subjectBlock"', HTML)
        self.assertIn('appCall("study_terms")', HTML)
        self.assertIn('"study_suggest_terms"', HTML)
        self.assertIn('appCall("study_set_term", term, confirmed)', HTML)
        # The list is shown before anonymisation, not after.
        self.assertIn("if (anonymizer) void loadSubjectTerms({suggest:true});", HTML)
        # The tick changes what counts as personal data,
        # so the findings are rebuilt.
        self.assertIn("if (state.raw) await askModel();", HTML)

    def test_work_is_written_to_the_project_file(self):
        self.assertIn('appCall(\n      "project_save_transcript"', HTML)
        self.assertIn('"project_save_turns"', HTML)
        self.assertIn('appCall("project_load")', HTML)
        self.assertIn('id="btnResumeProject"', HTML)
        # Autosave is called after every proofreading decision.
        self.assertIn("scheduleProjectSave();", HTML)

    def test_new_words_go_to_the_study_dictionary_first(self):
        # A word gets into the program's shared dictionary in a new version, and only
        # if it will be useful in more than one field. In the study dictionary it
        # works right away.
        # The person has already said "remember" — a second button isn't needed.
        self.assertNotIn('id="btnWordsToStudy"', HTML)
        self.assertIn('appCall("study_add_word", word)', HTML)
        self.assertIn("они уже добавлены в словарь", UI)
        self.assertIn("Отправить в общий словарь", UI)
        self.assertIn('id="proofStudyWords"', HTML)
        self.assertIn("\"study_remove_word\", button.parentElement.dataset.word", HTML)
        # The word is no longer unfamiliar — the remarks are recomputed.
        self.assertIn("void runProofreading();", HTML)

    def test_the_study_version_is_always_visible(self):
        self.assertIn('id="studyVersion"', HTML)
        self.assertIn('tr("study.version", {version: studyVersion.version})', HTML)
        self.assertEqual(RU["study.version"], "Файл исследования: версия {version}")
        # The file is read automatically when the folder is opened — there is no "refresh" button.
        self.assertNotIn('id="btnImportStudy"', HTML)
        # Team buttons are visible only when working from the lead's file.
        self.assertIn("STUDY.team = hasTeam;", HTML)
        self.assertIn('$("btnSendToTeam").hidden = !hasTeam;', HTML)

    def test_the_finished_interview_is_handed_over_by_a_file(self):
        # The program puts the file into the folder. It is sent by a script that
        # the lead put there too: the network is not its business.
        self.assertIn('id="btnSendToTeam"', HTML)
        self.assertIn('appCall("export_interview")', HTML)
        self.assertNotIn("XMLHttpRequest", HTML)

    def test_coding_is_not_offered_in_the_program(self):
        """Coding has moved to Radicans, a shared tool.

        It is collaborative by nature: the codebook is born at a meeting and
        changes all the time, and everyone must see the others' coding.
        Two coding systems mean two codebooks that will drift apart.

        The coding code is not removed from the program: until Radicans exists, it is needed."""

        self.assertIn(
            '<button class="mode-tab" id="tabCoding" role="tab"'
            ' aria-selected="false" hidden data-i18n="tab.coding">',
            HTML,
        )

    def test_the_coding_screen_is_codes_left_text_right(self):
        self.assertIn('id="codingView"', HTML)
        self.assertIn(".coding-layout{display:grid; grid-template-columns:280px 1fr", HTML)
        self.assertIn('id="codeList"', HTML)
        self.assertIn('id="codingSheet"', HTML)
        # The state is requested on every entry: anonymisation may have been
        # completed a minute ago.
        self.assertIn("if (coding) void loadCoding();", HTML)

    def test_only_the_respondent_answers_can_be_selected(self):
        # The interviewer's question is shown in full — without it the answer can't be read —
        # but there is nothing to select in it.
        self.assertIn("function respondentTurn(turn)", HTML)
        self.assertIn('data-codeable="1"', HTML)
        self.assertIn("if (!where || !where.dataset.codeable) return null;", HTML)
        self.assertIn(".coding-turn.asked .coding-turn-text{", HTML)
        self.assertIn("user-select:none", HTML)

    def test_marking_is_made_by_selecting_and_choosing_a_code(self):
        self.assertIn("function readSelection()", HTML)
        self.assertIn('appCall("coding_add"', HTML)
        self.assertIn('appCall("coding_remove", id)', HTML)
        # A note on a coding gets its own window, not prompt.
        self.assertIn('id="codingMemo"', HTML)
        self.assertIn("function editMemo(id)", HTML)

    def test_all_four_ways_of_looking_are_there(self):
        for look in ('data-look="document"', 'data-look="quotes"', 'data-look="code"'):
            with self.subTest(look=look):
                self.assertIn(look, HTML)
        # Dimming the uncoded text is the fourth one; it combines with any of them.
        self.assertIn('id="codingDim"', HTML)
        self.assertIn(".coding-sheet.dimmed .coding-plain{", HTML)
        self.assertIn("mark class=\"coding-mark\"", HTML)

    def test_the_respondent_gets_the_pseudonym_from_the_file_name(self):
        # Suggesting «Персона 1» ("Person 1") here would mean making
        # the person type in every time what is already written in the file name.
        self.assertIn("state.pseudonym = data.pseudonym", HTML)
        self.assertIn("if (entity.self) selfIds.add(", HTML)
        self.assertIn(
            "if (g.self && state.pseudonym){ g.suggest = state.pseudonym; continue; }",
            HTML,
        )
        # It's still a suggestion: the person makes the replacement with the button.
        self.assertIn('value="${escAttr(g.pseudonym || g.suggest)}"', HTML)

    def test_the_study_folder_is_not_a_document_manager(self):
        """Documents arrive as a file from the lead rather than being picked by hand.

        Everything needed for local processing the program takes from the folder
        itself. The «добавить документ» ("add document") buttons turned this into a second job."""

        for removed in ('id="studyDocs"', 'id="btnAddDocument"', '"study_add_document"'):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, HTML)

    def test_the_programme_is_not_asked_for_by_hand(self):
        # The guide and notes are enough for a single transcript; the study
        # programme arrives as a file from the lead, if there is one.
        self.assertNotIn('id="interviewProgramme"', HTML)
        self.assertNotIn("btnLoadProgramme", HTML)

    def test_gaps_in_numbering_are_not_an_error(self):
        # A sociologist's device holds only their own interviews.
        self.assertNotIn("В папке нет интервью с номерами", HTML)

    def test_a_missed_fragment_can_be_marked_by_hand(self):
        """The right column missed something — we mark it ourselves, with a right-click.

        A manual finding then behaves like any other: it goes into the
        list, gets a pseudonym for its kind, is replaced with the same
        button. There is deliberately no separate path for "manual"."""

        self.assertIn('id="anonMenu"', HTML)
        self.assertIn('$("text").addEventListener("contextmenu"', HTML)
        self.assertIn("function markSelection(cat, kind)", HTML)
        self.assertIn("const MANUAL_KINDS = [", HTML)
        # Place kinds are chosen right away: «Страна», «Регион», «Город», «Адрес» (country, region, city, address).
        for kind in ('kind:"country"', 'kind:"region"', 'kind:"city"', 'kind:"address"'):
            with self.subTest(kind=kind):
                self.assertIn(kind, HTML)
        # A kind chosen by hand beats what the model decided.
        self.assertIn("g.placeKind = ownKind || placeKindsByKey.get(g.key)", HTML)
        # All occurrences are added, not just the selected one.
        self.assertIn("while ((idx = state.raw.indexOf(frag, idx + 1)) !== -1)", HTML)

    def test_the_key_leaves_only_under_a_password(self):
        # The key restores personal data, so it never leaves the program
        # as plain text.
        self.assertIn('id="keyPassword"', HTML)
        self.assertIn("function askPassword()", HTML)
        self.assertIn(
            'appCall("save_key_archive", name, body, password, rows.length)', HTML
        )
        # The key is no longer put into the project as plain text.
        self.assertNotIn("project_save_key", HTML)
        self.assertNotIn('save("КЛЮЧ-" + baseName()', HTML)
        # An empty password is a silent refusal, not an unprotected file.
        self.assertIn("if (!password) return;", HTML)
        # The person is told what to open the archive with and that the password isn't stored.
        self.assertIn("7-Zip", UI)
        self.assertIn("Пароль нигде не хранится", UI)


if __name__ == "__main__":
    unittest.main()
