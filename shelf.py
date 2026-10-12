#!/usr/bin/env python3
"""shelf - put every captured thing on exactly one shelf, and show your working.

You have a folder. Screenshots, half-written notes, saved links, a photo of a
whiteboard. Somewhere in there are the things that belong to projects you are
actually working on, and the things somebody is waiting on you for.

shelf reads the folder, pulls text out of whatever it can, scores that text
against a project list YOU wrote, and writes a board. It runs on your machine,
needs no key, and installs nothing.

The design rule the whole thing is built around:

    EVERY ITEM LANDS ON EXACTLY ONE SHELF, AND THE BOARD SAYS WHY.

Not "mostly one". Exactly one. A triage tool that can silently drop an item, or
quietly file it twice, is worse than no triage tool, because you stop checking.
The partition is asserted on every run and the run fails if it does not hold.

Usage:
    python shelf.py build  --pile demo/pile --projects demo/projects.md
    python shelf.py ask    --pile demo/pile --projects demo/projects.md "invoice"
    python shelf.py explain --pile demo/pile --projects demo/projects.md note-3.txt
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# reading the pile
# --------------------------------------------------------------------------- #

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".note"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff"}

# How the text was obtained. This is reported per item, because a score built on
# a filename is worth a great deal less than one built on the actual contents,
# and the board should not pretend otherwise.
SOURCE_TEXT = "file contents"
SOURCE_OCR = "OCR"
SOURCE_SIDECAR = "sidecar .txt"
SOURCE_FILENAME = "filename only"


@dataclass
class Item:
    path: str
    name: str
    text: str
    source: str
    scores: dict = field(default_factory=dict)
    shelf: str = ""
    reason: str = ""
    owed: bool = False


def have_tesseract() -> bool:
    """Is the OCR binary on PATH? Absence is a supported configuration."""
    return shutil.which("tesseract") is not None


def ocr(path: str, timeout: int = 20) -> str:
    """Run tesseract as a subprocess. Returns '' on any failure, deliberately.

    A missing or broken OCR install must degrade the result, never crash the
    run - the fallback path below is a path you should try on purpose.
    """
    try:
        out = subprocess.run(
            ["tesseract", path, "stdout"],
            capture_output=True, text=True, timeout=timeout,
        )
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def words_from_filename(name: str) -> str:
    """A filename is a weak signal, but it is never nothing.

    'Screenshot 2026-10-02 invoice-chase.png' -> 'Screenshot invoice chase'
    Digits and timestamps carry no topic, so they go.
    """
    stem = os.path.splitext(name)[0]
    stem = re.sub(r"[_\-.]+", " ", stem)
    stem = re.sub(r"\b\d[\d:\-]*\b", " ", stem)
    return re.sub(r"\s+", " ", stem).strip()


def read_item(path: str, use_ocr: bool = True) -> Item:
    """Get the best text available for one file, and say where it came from."""
    name = os.path.basename(path)
    suffix = os.path.splitext(name)[1].lower()

    if suffix in TEXT_SUFFIXES:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return Item(path=path, name=name, text=fh.read(), source=SOURCE_TEXT)
        except OSError:
            return Item(path=path, name=name, text=words_from_filename(name),
                        source=SOURCE_FILENAME)

    if suffix in IMAGE_SUFFIXES:
        # a sidecar wins over OCR: a human wrote it, so it is the better evidence
        sidecar = os.path.splitext(path)[0] + ".txt"
        if os.path.exists(sidecar):
            try:
                with open(sidecar, "r", encoding="utf-8", errors="replace") as fh:
                    return Item(path=path, name=name, text=fh.read(), source=SOURCE_SIDECAR)
            except OSError:
                pass
        if use_ocr and have_tesseract():
            text = ocr(path)
            if text.strip():
                return Item(path=path, name=name, text=text, source=SOURCE_OCR)
        return Item(path=path, name=name, text=words_from_filename(name),
                    source=SOURCE_FILENAME)

    # anything else: the name is all we have
    return Item(path=path, name=name, text=words_from_filename(name), source=SOURCE_FILENAME)


def walk_pile(pile: str, use_ocr: bool = True) -> list:
    """Every file in the pile, as an Item. Sidecars are evidence, not items.

    A .txt sitting next to an image of the same stem is that image's caption. It
    must not also become an item of its own, or the count goes wrong and the
    partition becomes a lie.
    """
    if not os.path.isdir(pile):
        raise FileNotFoundError(f"no such pile: {pile}")

    paths = []
    for root, _dirs, files in os.walk(pile):
        for f in sorted(files):
            paths.append(os.path.join(root, f))

    image_stems = {
        os.path.splitext(p)[0] for p in paths
        if os.path.splitext(p)[1].lower() in IMAGE_SUFFIXES
    }

    items = []
    for p in paths:
        stem, suffix = os.path.splitext(p)
        if suffix.lower() == ".txt" and stem in image_stems:
            continue                      # it is a caption, not an item
        items.append(read_item(p, use_ocr=use_ocr))
    return items


# --------------------------------------------------------------------------- #
# the project list
# --------------------------------------------------------------------------- #

@dataclass
class Project:
    name: str
    terms: list


def parse_projects(path: str) -> list:
    """Read a markdown project list the user wrote.

        # Kitchen rebuild
        quote, builder, worktop, plumber

    The terms are the user's words. Nothing here invents a category, and nothing
    infers one from the pile - a classifier that writes its own labels is
    answering a question nobody asked.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()

    projects, current, terms = [], None, []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("#"):
            if current:
                projects.append(Project(current, terms))
            current, terms = line.lstrip("#").strip(), []
        elif line and current:
            for part in re.split(r"[,;]", line):
                part = part.strip().lower()
                if part:
                    terms.append(part)
    if current:
        projects.append(Project(current, terms))

    if not projects:
        raise ValueError(f"{path} declares no projects")
    names = [p.name for p in projects]
    if len(set(names)) != len(names):
        raise ValueError("two projects share a name")
    return projects


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #

WORD = re.compile(r"[a-z0-9']+")


def tokenise(text: str) -> list:
    return WORD.findall(text.lower())


def score_item(item: Item, projects: list) -> dict:
    """Term overlap, normalised by how many terms the project declared.

    Overlap, not similarity. OCR output is noisy and half-garbled, and a method
    that needs clean text to work is the wrong method for this input. Counting
    how many of YOUR words show up survives a bad scan; an edit distance does not.
    """
    tokens = tokenise(item.text)
    bag = set(tokens)
    joined = " ".join(tokens)

    out = {}
    for p in projects:
        if not p.terms:
            out[p.name] = 0.0
            continue
        hits = 0
        for term in p.terms:
            parts = term.split()
            if len(parts) == 1:
                if parts[0] in bag:
                    hits += 1
            elif term in joined:          # a multi-word term must appear in order
                hits += 1
        out[p.name] = hits / len(p.terms)
    return out


# Somebody is waiting on you. These are the shapes that say so, and they are
# checked against the text only - never against the filename, because a file
# called "reply-later.png" is a note to self, not a person waiting.
OWED_PATTERNS = [
    r"\bcan you\b", r"\bcould you\b", r"\bwaiting (on|for)\b", r"\bany update\b",
    r"\blet me know\b", r"\bplease (send|confirm|reply|advise)\b",
    r"\bgentle reminder\b", r"\bfollowing up\b", r"\bchasing\b", r"\bawaiting\b",
    r"\?\s*$",
]


def owes_reply(item: Item) -> bool:
    if item.source == SOURCE_FILENAME:
        return False                      # no real text, so no real evidence
    text = item.text.lower()
    return any(re.search(p, text, re.MULTILINE) for p in OWED_PATTERNS)


# --------------------------------------------------------------------------- #
# the partition
# --------------------------------------------------------------------------- #

UNFILED = "Unfiled"
OWED = "Replies you owe"


def assign(items: list, projects: list, threshold: float = 0.25) -> list:
    """Put every item on exactly one shelf, and record the reason.

    Order matters and is deliberate: an item somebody is waiting on goes to the
    replies shelf even if it also scores against a project, because the thing you
    need from the board is the thing with a person attached to it.
    """
    for item in items:
        item.scores = score_item(item, projects)
        item.owed = owes_reply(item)

        if item.owed:
            item.shelf = OWED
            item.reason = "the text reads as someone waiting on a reply"
            continue

        if not item.scores:
            item.shelf = UNFILED
            item.reason = "no projects were declared"
            continue

        best = max(item.scores.items(), key=lambda kv: (kv[1], kv[0]))
        if best[1] >= threshold:
            item.shelf = best[0]
            item.reason = f"matched {best[1]:.0%} of the terms for {best[0]}"
        else:
            item.shelf = UNFILED
            top = f"{best[0]} at {best[1]:.0%}" if best[1] else "nothing at all"
            item.reason = f"best match was {top}, under the {threshold:.0%} bar"
    return items


def check_partition(items: list, projects: list) -> None:
    """The invariant, asserted rather than hoped for."""
    valid = {p.name for p in projects} | {UNFILED, OWED}
    for item in items:
        if item.shelf not in valid:
            raise AssertionError(f"{item.name} landed on unknown shelf {item.shelf!r}")
        if not item.reason:
            raise AssertionError(f"{item.name} was filed with no reason given")

    shelved = {}
    for item in items:
        shelved.setdefault(item.shelf, []).append(item.name)
    total = sum(len(v) for v in shelved.values())
    if total != len(items):
        raise AssertionError(f"partition counts {total} items, the pile has {len(items)}")

    seen = [n for names in shelved.values() for n in names]
    if len(seen) != len(set(seen)):
        raise AssertionError("an item appears on more than one shelf")


# --------------------------------------------------------------------------- #
# the board
# --------------------------------------------------------------------------- #

def build_board(items: list, projects: list) -> str:
    order = [OWED] + [p.name for p in projects] + [UNFILED]
    by_shelf = {name: [] for name in order}
    for item in items:
        by_shelf[item.shelf].append(item)

    lines = ["# Board", ""]
    lines.append(f"{len(items)} items, {len(projects)} projects.")
    if not have_tesseract():
        lines.append("")
        lines.append("> tesseract is not installed, so images fell back to sidecars and "
                     "filenames. Scores built that way are marked, and they are weaker.")
    lines.append("")

    for name in order:
        bucket = by_shelf[name]
        lines.append(f"## {name}  ({len(bucket)})")
        if not bucket:
            lines.append("")
            lines.append("_nothing here_")
            lines.append("")
            continue
        lines.append("")
        lines.append("| item | read via | why |")
        lines.append("|---|---|---|")
        for item in sorted(bucket, key=lambda i: i.name):
            lines.append(f"| `{item.name}` | {item.source} | {item.reason} |")
        lines.append("")

    counts = {name: len(by_shelf[name]) for name in order}
    lines.append("## The partition")
    lines.append("")
    lines.append(" + ".join(f"{v} {k}" for k, v in counts.items() if v) +
                 f" = {sum(counts.values())} of {len(items)}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_build(args) -> int:
    projects = parse_projects(args.projects)
    items = walk_pile(args.pile, use_ocr=not args.no_ocr)
    assign(items, projects, threshold=args.threshold)
    check_partition(items, projects)
    board = build_board(items, projects)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(board)
    print(f"{len(items)} items -> {args.out}")
    if not have_tesseract():
        print("note: tesseract not found; images used sidecars or filenames")
    return 0


def cmd_ask(args) -> int:
    projects = parse_projects(args.projects)
    items = walk_pile(args.pile, use_ocr=not args.no_ocr)
    assign(items, projects, threshold=args.threshold)
    needle = " ".join(args.query).lower()
    hits = [i for i in items if needle in i.text.lower() or needle in i.name.lower()]
    if not hits:
        print(f"nothing in the pile mentions {needle!r}")
        return 1
    for item in sorted(hits, key=lambda i: i.name):
        print(f"{item.name}  [{item.shelf}]  via {item.source}")
    return 0


def cmd_explain(args) -> int:
    projects = parse_projects(args.projects)
    items = walk_pile(args.pile, use_ocr=not args.no_ocr)
    assign(items, projects, threshold=args.threshold)
    match = [i for i in items if i.name == args.item]
    if not match:
        print(f"no item named {args.item!r} in the pile")
        return 1
    item = match[0]
    print(f"{item.name}")
    print(f"  read via : {item.source}")
    print(f"  shelf    : {item.shelf}")
    print(f"  because  : {item.reason}")
    print(f"  owed     : {'yes' if item.owed else 'no'}")
    print("  scores   :")
    for name, sc in sorted(item.scores.items(), key=lambda kv: -kv[1]):
        print(f"    {sc:6.0%}  {name}")
    return 0


def main(argv=None) -> int:
    # The shared flags live on a parent parser so they are accepted on EITHER
    # side of the subcommand. argparse puts them only before it otherwise, and
    # `shelf build --pile x` is the order everybody actually types.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--pile", default="demo/pile")
    common.add_argument("--projects", default="demo/projects.md")
    common.add_argument("--threshold", type=float, default=0.25)
    common.add_argument("--no-ocr", action="store_true", help="skip tesseract even if present")

    ap = argparse.ArgumentParser(prog="shelf", parents=[common],
                                 description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", parents=[common], help="write the board")
    b.add_argument("--out", default="board.md")
    b.set_defaults(func=cmd_build)

    a = sub.add_parser("ask", parents=[common], help="search the pile")
    a.add_argument("query", nargs="+")
    a.set_defaults(func=cmd_ask)

    e = sub.add_parser("explain", parents=[common], help="why did this item land where it did")
    e.add_argument("item")
    e.set_defaults(func=cmd_explain)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
