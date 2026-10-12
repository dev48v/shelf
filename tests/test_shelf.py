"""Tests for shelf.

The suite is built around the one promise the tool makes:

    every item lands on exactly one shelf, and the board says why.

So the partition gets its own section, and it is checked on generated piles as
well as on hand-written ones - a property that only holds on the examples you
thought of is not a property.

    python -m unittest discover -s tests -v
"""

import os
import random
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shelf  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class PileTestCase(unittest.TestCase):
    """A scratch pile per test, torn down afterwards."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="shelf-test-")
        self.pile = os.path.join(self.dir, "pile")
        os.makedirs(self.pile)
        self.projects_path = os.path.join(self.dir, "projects.md")
        write(self.projects_path, "# Alpha\nwidget, sprocket\n\n# Beta\nflange, gasket\n")
        self.projects = shelf.parse_projects(self.projects_path)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def item(self, name, text):
        write(os.path.join(self.pile, name), text)

    def run_pile(self, threshold=0.25):
        items = shelf.walk_pile(self.pile, use_ocr=False)
        shelf.assign(items, self.projects, threshold=threshold)
        return items


# --------------------------------------------------------------------------- #
# the project list
# --------------------------------------------------------------------------- #

class TestProjects(PileTestCase):

    def test_parses_headings_and_terms(self):
        self.assertEqual([p.name for p in self.projects], ["Alpha", "Beta"])
        self.assertEqual(self.projects[0].terms, ["widget", "sprocket"])

    def test_terms_are_lowercased(self):
        write(self.projects_path, "# Gamma\nWIDGET, Sprocket\n")
        p = shelf.parse_projects(self.projects_path)[0]
        self.assertEqual(p.terms, ["widget", "sprocket"])

    def test_semicolons_also_separate(self):
        write(self.projects_path, "# Gamma\na; b; c\n")
        self.assertEqual(shelf.parse_projects(self.projects_path)[0].terms, ["a", "b", "c"])

    def test_terms_may_span_several_lines(self):
        write(self.projects_path, "# Gamma\na, b\nc, d\n")
        self.assertEqual(shelf.parse_projects(self.projects_path)[0].terms, ["a", "b", "c", "d"])

    def test_empty_list_is_refused(self):
        write(self.projects_path, "nothing but prose\n")
        with self.assertRaises(ValueError):
            shelf.parse_projects(self.projects_path)

    def test_duplicate_project_names_are_refused(self):
        write(self.projects_path, "# Alpha\na\n\n# Alpha\nb\n")
        with self.assertRaises(ValueError):
            shelf.parse_projects(self.projects_path)

    def test_a_project_with_no_terms_is_allowed_and_scores_zero(self):
        write(self.projects_path, "# Alpha\nwidget\n\n# Empty\n")
        projects = shelf.parse_projects(self.projects_path)
        self.item("a.txt", "widget widget")
        items = shelf.walk_pile(self.pile, use_ocr=False)
        shelf.assign(items, projects)
        self.assertEqual(items[0].scores["Empty"], 0.0)


# --------------------------------------------------------------------------- #
# reading the pile
# --------------------------------------------------------------------------- #

class TestReading(PileTestCase):

    def test_text_files_are_read_whole(self):
        self.item("a.txt", "widget and sprocket")
        it = self.run_pile()[0]
        self.assertEqual(it.source, shelf.SOURCE_TEXT)
        self.assertIn("sprocket", it.text)

    def test_markdown_counts_as_text(self):
        self.item("a.md", "# heading\nwidget")
        self.assertEqual(self.run_pile()[0].source, shelf.SOURCE_TEXT)

    def test_a_sidecar_is_preferred_over_the_filename(self):
        self.item("shot.png", "binary-ish")
        self.item("shot.txt", "widget sprocket")
        items = self.run_pile()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].name, "shot.png")
        self.assertEqual(items[0].source, shelf.SOURCE_SIDECAR)

    def test_a_sidecar_is_not_itself_an_item(self):
        """The count is the whole point: a caption must not be double-counted."""
        self.item("shot.png", "x")
        self.item("shot.txt", "widget")
        self.item("other.txt", "sprocket")
        names = sorted(i.name for i in self.run_pile())
        self.assertEqual(names, ["other.txt", "shot.png"])

    def test_an_image_with_no_sidecar_falls_back_to_the_filename(self):
        self.item("holiday-widget-photo.png", "x")
        it = self.run_pile()[0]
        self.assertEqual(it.source, shelf.SOURCE_FILENAME)
        self.assertIn("widget", it.text)

    def test_filenames_drop_digits_and_separators(self):
        got = shelf.words_from_filename("Screenshot 2026-10-02_invoice-chase.png")
        self.assertNotIn("2026", got)
        self.assertIn("invoice", got)
        self.assertIn("chase", got)

    def test_unknown_suffixes_use_the_filename(self):
        self.item("widget-plan.dwg", "binary")
        it = self.run_pile()[0]
        self.assertEqual(it.source, shelf.SOURCE_FILENAME)

    def test_nested_folders_are_walked(self):
        self.item(os.path.join("sub", "deep.txt"), "widget")
        self.assertEqual(len(self.run_pile()), 1)

    def test_a_missing_pile_raises(self):
        with self.assertRaises(FileNotFoundError):
            shelf.walk_pile(os.path.join(self.dir, "nope"))

    def test_an_empty_pile_is_not_an_error(self):
        self.assertEqual(self.run_pile(), [])

    def test_ocr_returns_empty_rather_than_raising_when_absent(self):
        """A broken OCR install must degrade the result, never kill the run."""
        self.assertEqual(shelf.ocr(os.path.join(self.dir, "does-not-exist.png")), "")


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #

class TestScoring(PileTestCase):

    def test_score_is_the_fraction_of_terms_hit(self):
        self.item("a.txt", "widget only")
        it = self.run_pile()[0]
        self.assertAlmostEqual(it.scores["Alpha"], 0.5)

    def test_all_terms_gives_one(self):
        self.item("a.txt", "widget sprocket")
        self.assertAlmostEqual(self.run_pile()[0].scores["Alpha"], 1.0)

    def test_no_terms_gives_zero(self):
        self.item("a.txt", "nothing relevant here")
        self.assertAlmostEqual(self.run_pile()[0].scores["Alpha"], 0.0)

    def test_scoring_ignores_case(self):
        self.item("a.txt", "WIDGET SPROCKET")
        self.assertAlmostEqual(self.run_pile()[0].scores["Alpha"], 1.0)

    def test_a_term_counts_once_however_often_it_appears(self):
        self.item("a.txt", "widget widget widget widget")
        self.assertAlmostEqual(self.run_pile()[0].scores["Alpha"], 0.5)

    def test_partial_words_do_not_count(self):
        """'widgets' is not 'widget'. Overlap is on whole tokens."""
        self.item("a.txt", "widgetry and widgets")
        self.assertAlmostEqual(self.run_pile()[0].scores["Alpha"], 0.0)

    def test_multi_word_terms_must_appear_in_order(self):
        write(self.projects_path, "# Alpha\nself assessment\n")
        projects = shelf.parse_projects(self.projects_path)
        self.item("yes.txt", "my self assessment form")
        self.item("no.txt", "assessment of self")
        items = shelf.walk_pile(self.pile, use_ocr=False)
        shelf.assign(items, projects)
        by = {i.name: i for i in items}
        self.assertAlmostEqual(by["yes.txt"].scores["Alpha"], 1.0)
        self.assertAlmostEqual(by["no.txt"].scores["Alpha"], 0.0)

    def test_scoring_survives_garbled_text(self):
        """The method has to work on bad OCR, which is the whole reason for it."""
        self.item("a.txt", "w1dget !!! spr0cket ... widget ~~~ sprocket")
        self.assertAlmostEqual(self.run_pile()[0].scores["Alpha"], 1.0)

    def test_every_project_gets_a_score(self):
        self.item("a.txt", "widget")
        self.assertEqual(set(self.run_pile()[0].scores), {"Alpha", "Beta"})


# --------------------------------------------------------------------------- #
# replies you owe
# --------------------------------------------------------------------------- #

class TestOwed(PileTestCase):

    def test_a_question_is_owed(self):
        self.item("a.txt", "could you send the widget spec?")
        self.assertTrue(self.run_pile()[0].owed)

    def test_chasing_language_is_owed(self):
        self.item("a.txt", "just following up on this")
        self.assertTrue(self.run_pile()[0].owed)

    def test_a_plain_note_is_not_owed(self):
        self.item("a.txt", "widget arrived, looks fine")
        self.assertFalse(self.run_pile()[0].owed)

    def test_owed_beats_a_project_match(self):
        self.item("a.txt", "widget sprocket - can you confirm?")
        it = self.run_pile()[0]
        self.assertEqual(it.shelf, shelf.OWED)
        self.assertAlmostEqual(it.scores["Alpha"], 1.0)

    def test_a_filename_alone_never_counts_as_owed(self):
        """'reply-to-this.png' is a note to self, not a person waiting."""
        self.item("can-you-reply-to-this.png", "x")
        it = self.run_pile()[0]
        self.assertEqual(it.source, shelf.SOURCE_FILENAME)
        self.assertFalse(it.owed)


# --------------------------------------------------------------------------- #
# the partition - the promise the tool makes
# --------------------------------------------------------------------------- #

class TestPartition(PileTestCase):

    def test_every_item_gets_a_shelf_and_a_reason(self):
        for n, t in [("a.txt", "widget sprocket"), ("b.txt", "flange"),
                     ("c.txt", "nothing at all"), ("d.txt", "can you check?")]:
            self.item(n, t)
        items = self.run_pile()
        shelf.check_partition(items, self.projects)
        for it in items:
            self.assertTrue(it.shelf)
            self.assertTrue(it.reason)

    def test_counts_add_up(self):
        for i in range(12):
            self.item(f"n{i}.txt", ["widget sprocket", "flange gasket", "unrelated"][i % 3])
        items = self.run_pile()
        shelf.check_partition(items, self.projects)
        buckets = {}
        for it in items:
            buckets.setdefault(it.shelf, 0)
            buckets[it.shelf] += 1
        self.assertEqual(sum(buckets.values()), len(items))

    def test_partition_holds_on_generated_piles(self):
        """A property that holds only on hand-picked examples is not a property."""
        words = ["widget", "sprocket", "flange", "gasket", "banana", "tuesday",
                 "can you", "following up", "invoice", "quote"]
        rnd = random.Random(20261012)
        for trial in range(40):
            d = tempfile.mkdtemp(prefix="shelf-fuzz-")
            try:
                pile = os.path.join(d, "pile")
                os.makedirs(pile)
                n = rnd.randint(0, 9)
                for i in range(n):
                    body = " ".join(rnd.choice(words) for _ in range(rnd.randint(0, 8)))
                    suffix = rnd.choice([".txt", ".md", ".png", ".dat"])
                    write(os.path.join(pile, f"f{i}{suffix}"), body)
                items = shelf.walk_pile(pile, use_ocr=False)
                shelf.assign(items, self.projects,
                             threshold=rnd.choice([0.0, 0.25, 0.5, 1.0]))
                shelf.check_partition(items, self.projects)
                self.assertEqual(len(items), n, f"trial {trial}: item count changed")
            finally:
                shutil.rmtree(d, ignore_errors=True)

    def test_check_partition_catches_a_bad_shelf(self):
        self.item("a.txt", "widget")
        items = self.run_pile()
        items[0].shelf = "Somewhere else"
        with self.assertRaises(AssertionError):
            shelf.check_partition(items, self.projects)

    def test_check_partition_catches_a_missing_reason(self):
        self.item("a.txt", "widget")
        items = self.run_pile()
        items[0].reason = ""
        with self.assertRaises(AssertionError):
            shelf.check_partition(items, self.projects)

    def test_threshold_moves_items_between_project_and_unfiled(self):
        self.item("a.txt", "widget")                 # 50% of Alpha
        self.assertEqual(self.run_pile(threshold=0.25)[0].shelf, "Alpha")
        self.assertEqual(self.run_pile(threshold=0.75)[0].shelf, shelf.UNFILED)

    def test_a_threshold_of_one_demands_every_term(self):
        self.item("a.txt", "widget")
        self.item("b.txt", "widget sprocket")
        got = {i.name: i.shelf for i in self.run_pile(threshold=1.0)}
        self.assertEqual(got["a.txt"], shelf.UNFILED)
        self.assertEqual(got["b.txt"], "Alpha")

    def test_ties_break_deterministically(self):
        write(self.projects_path, "# Alpha\nshared\n\n# Beta\nshared\n")
        projects = shelf.parse_projects(self.projects_path)
        self.item("a.txt", "shared")
        shelves = set()
        for _ in range(10):
            items = shelf.walk_pile(self.pile, use_ocr=False)
            shelf.assign(items, projects)
            shelves.add(items[0].shelf)
        self.assertEqual(len(shelves), 1, "a tie must not wander between runs")


# --------------------------------------------------------------------------- #
# the board
# --------------------------------------------------------------------------- #

class TestBoard(PileTestCase):

    def test_board_names_every_shelf_even_when_empty(self):
        self.item("a.txt", "widget sprocket")
        items = self.run_pile()
        board = shelf.build_board(items, self.projects)
        self.assertIn("## Alpha", board)
        self.assertIn("## Beta", board)
        self.assertIn("_nothing here_", board)

    def test_board_lists_every_item_exactly_once(self):
        for i in range(6):
            self.item(f"n{i}.txt", "widget sprocket" if i % 2 else "unrelated")
        items = self.run_pile()
        board = shelf.build_board(items, self.projects)
        for it in items:
            self.assertEqual(board.count(f"`{it.name}`"), 1, it.name)

    def test_board_reports_the_partition_sum(self):
        self.item("a.txt", "widget sprocket")
        items = self.run_pile()
        board = shelf.build_board(items, self.projects)
        self.assertIn(f"= {len(items)} of {len(items)}", board)

    def test_board_says_how_each_item_was_read(self):
        self.item("a.txt", "widget sprocket")
        self.item("b.png", "x")
        board = shelf.build_board(self.run_pile(), self.projects)
        self.assertIn(shelf.SOURCE_TEXT, board)
        self.assertIn(shelf.SOURCE_FILENAME, board)

    def test_board_on_an_empty_pile_is_still_a_board(self):
        board = shelf.build_board([], self.projects)
        self.assertIn("0 items", board)
        self.assertIn("## Alpha", board)


# --------------------------------------------------------------------------- #
# the command line
# --------------------------------------------------------------------------- #

class TestCLI(PileTestCase):

    def test_build_writes_a_board(self):
        self.item("a.txt", "widget sprocket")
        out = os.path.join(self.dir, "board.md")
        rc = shelf.main(["build", "--pile", self.pile,
                         "--projects", self.projects_path, "--out", out, "--no-ocr"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(out))

    def test_flags_work_on_either_side_of_the_subcommand(self):
        self.item("a.txt", "widget")
        out = os.path.join(self.dir, "b.md")
        rc = shelf.main(["--pile", self.pile, "--projects", self.projects_path,
                         "--no-ocr", "build", "--out", out])
        self.assertEqual(rc, 0)

    def test_ask_finds_a_hit(self):
        self.item("a.txt", "widget sprocket")
        rc = shelf.main(["ask", "--pile", self.pile,
                         "--projects", self.projects_path, "--no-ocr", "sprocket"])
        self.assertEqual(rc, 0)

    def test_ask_reports_a_miss_with_a_non_zero_code(self):
        self.item("a.txt", "widget")
        rc = shelf.main(["ask", "--pile", self.pile,
                         "--projects", self.projects_path, "--no-ocr", "banana"])
        self.assertEqual(rc, 1)

    def test_explain_reports_an_unknown_item(self):
        self.item("a.txt", "widget")
        rc = shelf.main(["explain", "--pile", self.pile,
                         "--projects", self.projects_path, "--no-ocr", "nope.txt"])
        self.assertEqual(rc, 1)

    def test_explain_succeeds_on_a_real_item(self):
        self.item("a.txt", "widget")
        rc = shelf.main(["explain", "--pile", self.pile,
                         "--projects", self.projects_path, "--no-ocr", "a.txt"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
