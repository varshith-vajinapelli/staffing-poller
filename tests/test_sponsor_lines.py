"""
Tests for keeping sponsorship lines that sit past the AI's 6000-character cut.

Run:  python -m unittest discover tests
"""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import poller  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
NO_FUTURE = ("Applicants for employment in the US must have work authorization that does "
             "not now or in the future require sponsorship of a visa for employment "
             "authorization in the United States.")


def filler(n):
    # Plain text with no visa words, n characters long.
    return ("We build reliable software for our clients every single day. " * 1000)[:n]


def read_fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


class AiTextTests(unittest.TestCase):

    # --- the bug that started this ---

    def test_accenture_line_past_cut_reaches_ai(self):
        desc = read_fixture("accenture_test_automation.txt")
        flat = re.sub(r"\s+", " ", desc)
        self.assertGreater(flat.find("now or in the future"), poller.DESC_LIMIT)
        self.assertIn("now or in the future require sponsorship", poller.ai_text(desc))

    def test_accenture_text_stays_small(self):
        desc = read_fixture("accenture_test_automation.txt")
        self.assertLessEqual(len(poller.ai_text(desc)), poller.DESC_LIMIT + poller.EXTRA_LIMIT + 10)

    def test_accenture_eeo_boilerplate_not_pulled_in(self):
        # "citizenship status" in the EEO paragraph is not a work-visa line.
        extra = poller.ai_text(read_fixture("accenture_test_automation.txt"))[poller.DESC_LIMIT:]
        self.assertNotIn("veteran status", extra)

    # --- nothing changes when there's nothing to add ---

    def test_short_posting_unchanged(self):
        desc = "Junior developer. " + NO_FUTURE
        self.assertEqual(poller.ai_text(desc), desc)

    def test_long_posting_without_visa_words_is_plain_cut(self):
        desc = filler(9000)
        self.assertEqual(poller.ai_text(desc), desc[:poller.DESC_LIMIT])

    def test_line_already_inside_cut_not_duplicated(self):
        desc = NO_FUTURE + " " + filler(9000)
        self.assertEqual(poller.ai_text(desc).count("sponsorship"), 1)

    def test_empty(self):
        self.assertEqual(poller.ai_text(""), "")

    def test_whitespace_collapsed(self):
        self.assertEqual(poller.ai_text("a\n\n  b\tc"), "a b c")

    # --- not too loose: other phrasings past the cut are caught ---

    def test_case_insensitive(self):
        desc = filler(7000) + " NO VISA SPONSORSHIP IS AVAILABLE FOR THIS ROLE."
        self.assertIn("NO VISA SPONSORSHIP", poller.ai_text(desc))

    def test_us_abbreviation_does_not_cut_line(self):
        desc = filler(7000) + " Must be authorized to work in the U.S. without sponsorship now or in the future."
        self.assertIn("U.S. without sponsorship now or in the future", poller.ai_text(desc))

    def test_h1b_and_authorized_to_work(self):
        desc = filler(7000) + " We cannot support H-1B transfers. " + filler(500) + \
            " Candidates must be legally authorized to work in the United States."
        out = poller.ai_text(desc)
        self.assertIn("H-1B", out)
        self.assertIn("authorized to work", out)

    def test_opt_word(self):
        desc = filler(7000) + " Candidates on OPT or CPT are not eligible."
        self.assertIn("OPT or CPT", poller.ai_text(desc))

    def test_good_news_line_also_kept(self):
        # Not only bad lines: "we sponsor" past the cut should reach the AI too.
        desc = filler(7000) + " This role is eligible for visa sponsorship."
        self.assertIn("eligible for visa sponsorship", poller.ai_text(desc))

    # --- not too strict / noisy: look-alike words don't trigger ---

    def test_lookalike_words_ignored(self):
        desc = filler(7000) + " Optimize options. Adopt tools. Our sponsors? No: event partners."
        out = poller.ai_text(desc)
        self.assertNotIn("Optimize", out)
        self.assertNotIn("Adopt", out)

    def test_extra_capped(self):
        desc = filler(7000) + (" Visa sponsorship is not available. " + filler(400)) * 30
        self.assertLessEqual(len(poller.ai_text(desc)), poller.DESC_LIMIT + poller.EXTRA_LIMIT + 10)


class PromptTests(unittest.TestCase):
    """ask_gemini must send the kept line in the real request."""

    def setUp(self):
        self.sent = []
        self.old_fetch = poller.fetch
        self.old_key = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "test"

        def fake_fetch(url, data=None, timeout=30, retry=True):
            self.sent.append(data)
            answer = [{"id": "x1", "verdict": "skip", "reason": "no future sponsorship"}]
            return json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}}]})

        poller.fetch = fake_fetch

    def tearDown(self):
        poller.fetch = self.old_fetch
        if self.old_key is None:
            del os.environ["GEMINI_API_KEY"]
        else:
            os.environ["GEMINI_API_KEY"] = self.old_key

    def test_prompt_contains_line(self):
        job = {"id": "x1", "title": "Test Automation Engineer", "firm": "Accenture",
               "location": "Charlotte", "link": "https://example.com",
               "desc": read_fixture("accenture_test_automation.txt")}
        poller.ask_gemini([job])
        body = self.sent[0]
        if isinstance(body, bytes):
            body = body.decode()
        if not isinstance(body, str):
            body = json.dumps(body)
        self.assertIn("now or in the future require sponsorship", body)


class DescribeTests(unittest.TestCase):
    """ai_review must keep the full page text so ask_gemini can find the line."""

    def test_describer_text_not_cut_early(self):
        long_text = filler(7000) + " " + NO_FUTURE
        old = dict(poller.DESCRIBERS)
        old_key = os.environ.pop("GEMINI_API_KEY", None)
        poller.DESCRIBERS["FakeFirm"] = lambda job: long_text
        try:
            job = {"id": "f1", "title": "Dev", "firm": "FakeFirm", "location": "", "desc": ""}
            poller.ai_review([job])  # no key: describes, then stops before the AI
            self.assertIn("now or in the future", job["desc"])
        finally:
            poller.DESCRIBERS.clear()
            poller.DESCRIBERS.update(old)
            if old_key is not None:
                os.environ["GEMINI_API_KEY"] = old_key


if __name__ == "__main__":
    unittest.main()
