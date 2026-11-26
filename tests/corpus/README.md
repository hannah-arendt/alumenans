# Test corpus

Real recordings that the pipeline is run on, stage by stage. `tools/corpus.py`
does the work; `tests/test_corpus.py` runs the same checks as tests and skips
them on a machine that has no recordings.

- `private/` is ignored by git. Recordings that must not be published go here.
- `public/` is committed. It is empty until there are recordings that can be shared.

Another folder of cases can be added with the `ALUMENANS_CORPUS` environment
variable.

## One case

```
private/my-interview/
  case.toml                  what the recording is (written by `new`)
  recording.m4a              the recording
  guide.txt, notes.txt       optional: the interview guide, the researcher's notes
  cache/transcript.json      machine transcript (written by `transcribe`)
  reference.txt              the transcript as a person hears it, personal data marked
  expected/<stage>.json      accepted output of each stage (written by `accept`)
  expected/baseline.json     accepted quality numbers (written by `accept`)
  out/<stage>.json           current output, for looking at a diff (ignored by git)
```

Three roles: a person provides `case.toml`, the recording, the guide and
`reference.txt`; the machine produces `cache/` and `expected/`, which are
committed for a public case; `out/` is scratch.

## Adding a recording

```
make corpus A="new my-interview"         # creates private/my-interview/case.toml
#   copy the recording into the folder, set its name and the speaker count in case.toml
make corpus A="transcribe my-interview"  # slow: speech recognition, once
make corpus A="accept my-interview"      # records the current output as the starting point
make corpus A="draft my-interview"       # writes a first reference.txt to correct
```

The speech models are taken from this checkout if it has them, otherwise from
an installed copy of the app. `--models DIR` or `ALUMENANS_MODELS` names
another folder.

## The reference

`reference.txt` is what the quality numbers are measured against, so a person
has to make it. `draft` writes a starting point: the machine transcript, one
line per turn, with the personal data the program found already marked.

```
[00:04.0] Спикер 2: Меня зовут {{person:Мария Иванова}}, я живу в {{place:Казани}}.
[00:12.0] Спикер 1: Вы знакомы с {{person:Нютой Федермессер}}?
[00:16.5]
```

Correct it while listening to the recording:

- **Words.** Write what was said, including what the program missed.
- **Speakers.** Fix the number after «Спикер» where the voice is someone else's.
  If two people speak within one line, split it into two lines; an approximate
  timecode for the new line is enough.
- **Personal data.** Every name, place, organisation, contact, age or date of
  birth goes in `{{category:fragment}}`, whether or not you would mask it.
  Categories: `person`, `org`, `place`, `contact`, `age`, `birth`, `other`.
  Add marks the program missed, delete marks that are wrong, fix wrong categories.
  The mark says what the fragment is. Whether to mask it is the researcher's
  decision in each study and is not recorded here: the program is judged on
  finding and classifying, not on guessing that decision.
- The last line is a lone timecode: where the recording ends.

Because the draft starts from the machine's own words, the error rate measured
against it comes out a little too low: an error nobody noticed stays in the
reference.

## Checking

```
make corpus A=run        # what changed since the accepted output, with a diff
make corpus A=metrics    # the quality numbers, next to the baseline
make corpus A=accept     # the current output and numbers become the accepted ones
make test T=tests.test_corpus
```

A changed stage output is not necessarily a regression. It says the output is
different: read the diff, and accept it if the change is intended. The quality
numbers are the judge: the test fails when one of them is worse than the
baseline by more than the margin (`margin` in `case.toml`, 0.01 by default).

## What is not covered yet

Half of anonymization (contacts and identification numbers, grouping, pseudonyms,
the replacement itself) runs in the page's JavaScript, which these tests cannot
call. The `detection` stage and the detection numbers cover the Python half only.
