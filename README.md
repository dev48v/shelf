# shelf

Point it at a folder of captured things — screenshots, half-written notes, a
photo of a whiteboard — and it puts every one of them on exactly one shelf,
with a reason you can read.

```
$ python shelf.py build --pile demo/pile --projects demo/projects.md
6 items -> board.md
note: tesseract not found; images used sidecars or filenames
```

```markdown
## Replies you owe  (1)
| item | read via | why |
|---|---|---|
| `email-accountant.txt` | file contents | the text reads as someone waiting on a reply |

## Kitchen rebuild  (2)
| `note-quote.txt`  | file contents | matched 83% of the terms for Kitchen rebuild |
| `whiteboard.png`  | sidecar .txt  | matched 50% of the terms for Kitchen rebuild |

## The partition
1 Replies you owe + 2 Kitchen rebuild + 1 Cycling trip + 2 Unfiled = 6 of 6
```

Runs on your machine, on your folder. No key, no account, no install.

---

## The one rule it is built around

> **Every item lands on exactly one shelf, and the board says why.**

Not *mostly* one. Exactly one. A triage tool that can silently drop an item, or
quietly file it twice, is worse than no triage tool — because you stop checking
it, and then it is just another pile.

`check_partition()` asserts this on every run and the run fails if it does not
hold. The board prints the sum at the bottom so you can see it held.

## What it does

| Step | What happens |
|---|---|
| **Read** | `.txt`/`.md` read whole. Images try a sidecar `.txt`, then `tesseract` if installed, then the filename. **Every item reports which**, because a score built on a filename is worth far less than one built on contents. |
| **Score** | Term overlap against the project list *you* wrote. How many of your words show up, over how many you declared. |
| **Owe** | Text that reads as somebody waiting on a reply goes to its own shelf first, ahead of any project match. |
| **File** | Highest score above the threshold wins; everything else is `Unfiled`, never silently dropped. |

## Why term overlap, and not something cleverer

OCR output is garbled. Half a word is missing, `0` is `O`, a line is nonsense.
Counting how many of *your* terms survive is robust to exactly that kind of
damage. An edit distance or an embedding needs clean text to behave, and clean
text is the one thing a pile of screenshots does not have.

`test_scoring_survives_garbled_text` pins this down: `w1dget !!! spr0cket ...
widget ~~~ sprocket` still scores 100%.

## The categories are yours

```markdown
# Kitchen rebuild
quote, builder, worktop, plumber, tiles, splashback

# Tax return
invoice, receipt, hmrc, expenses, self assessment, accountant
```

Headings are shelves, the lines under them are terms. Nothing here invents a
category or infers one from the pile. A classifier that writes its own labels is
answering a question you did not ask.

Multi-word terms must appear **in order** — `self assessment` matches "my self
assessment form" and not "assessment of self".

## Commands

```bash
python shelf.py build   --pile demo/pile --projects demo/projects.md   # write board.md
python shelf.py ask     --pile demo/pile --projects demo/projects.md invoice
python shelf.py explain --pile demo/pile --projects demo/projects.md note-quote.txt
```

`explain` prints the score against every project and the reason the item landed
where it did. If you disagree with a filing, that is the command that tells you
whether to change your terms or your threshold.

Flags work on either side of the subcommand.

| Flag | Default | |
|---|---|---|
| `--threshold` | `0.25` | fraction of a project's terms needed to file against it |
| `--no-ocr` | off | skip tesseract even when it is installed — try this path on purpose |
| `--out` | `board.md` | where `build` writes |

## Running it

```bash
git clone https://github.com/dev48v/shelf
cd shelf
python shelf.py build --pile demo/pile --projects demo/projects.md
python -m unittest discover -s tests -v     # 51 tests
```

Python 3.10+. Nothing to install. `tesseract` is optional:
`brew install tesseract` / `choco install tesseract`. Without it, images fall
back to sidecars and filenames, the board says so at the top, and every affected
row is marked.

## What it does not do

- **It does not read images without help.** No tesseract and no sidecar means
  the filename is all there is, and the board admits it rather than guessing.
- **It does not learn.** The terms are yours and they stay yours.
- **It does not move your files.** It only ever reads, and writes one markdown
  file.
- **It has no opinion about priority.** Shelves, not a ranking.

## Tests

51, in `tests/test_shelf.py`, grouped by what they protect. The partition gets
its own section and is checked on **generated** piles as well as hand-written
ones — a property that only holds on the examples you thought of is not a
property.

```
Ran 51 tests in 2.6s
OK
```

---

Agent Lab · Vol 5 #01. MIT © Devanshu Biswas.
