"""
Tests for reading iCIMS and Workable job pages (both are built by JavaScript,
so a plain fetch of the link gets an almost empty page).

Run:  python -m unittest discover tests
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import poller  # noqa: E402
import company_poller  # noqa: E402

SCHWAB = "https://career-schwab.icims.com/jobs/127949/job?mobile=true&needsRedirect=false"
COOK = "https://americas-cookmedical.icims.com/jobs/20446/job?mobile=true&needsRedirect=false"
WORKABLE = "https://apply.workable.com/elevation-autism-center/j/B5CCC7D40B/apply"


class UrlTests(unittest.TestCase):

    def test_icims_url(self):
        self.assertEqual(company_poller.icims_url(SCHWAB),
                         "https://career-schwab.icims.com/jobs/127949/job?in_iframe=1")

    def test_icims_url_other_firm(self):
        self.assertEqual(company_poller.icims_url(COOK),
                         "https://americas-cookmedical.icims.com/jobs/20446/job?in_iframe=1")

    def test_icims_url_with_title_in_path(self):
        link = "https://careers-acme.icims.com/jobs/555/software-engineer/job?hub=7"
        self.assertEqual(company_poller.icims_url(link),
                         "https://careers-acme.icims.com/jobs/555/software-engineer/job?in_iframe=1")

    def test_icims_url_not_icims(self):
        self.assertEqual(company_poller.icims_url(WORKABLE), "")

    def test_workable_url(self):
        self.assertEqual(company_poller.workable_url(WORKABLE),
                         "https://apply.workable.com/api/v2/accounts/elevation-autism-center/jobs/B5CCC7D40B")

    def test_workable_url_without_apply(self):
        self.assertEqual(company_poller.workable_url("https://apply.workable.com/acme/j/ABC123/"),
                         "https://apply.workable.com/api/v2/accounts/acme/jobs/ABC123")

    def test_workable_url_not_workable(self):
        self.assertEqual(company_poller.workable_url(SCHWAB), "")


class PageTextTests(unittest.TestCase):
    """page_text must fetch the readable version, not the JavaScript shell."""

    def setUp(self):
        self.asked = []
        self.old_fetch = poller.fetch

    def tearDown(self):
        poller.fetch = self.old_fetch

    def fake(self, reply):
        def fetch(url, data=None, timeout=30, retry=True):
            self.asked.append(url)
            return reply
        poller.fetch = fetch

    def test_icims_fetches_iframe_page(self):
        self.fake("<html><script>var x=1;</script><div>Requirements: C++ and Python. "
                  "No sponsorship now or in the future.</div></html>")
        text = company_poller.page_text({"link": SCHWAB})
        self.assertEqual(self.asked, ["https://career-schwab.icims.com/jobs/127949/job?in_iframe=1"])
        self.assertIn("No sponsorship now or in the future", text)
        self.assertNotIn("var x", text)

    def test_workable_reads_all_parts(self):
        self.fake(json.dumps({"title": "Software Engineer",
                              "description": "<p>Build tools.</p>",
                              "requirements": "<ul><li>Python</li></ul>",
                              "benefits": "<p>Health insurance</p>"}))
        text = company_poller.page_text({"link": WORKABLE})
        self.assertEqual(self.asked, [company_poller.workable_url(WORKABLE)])
        for part in ["Build tools.", "Python", "Health insurance"]:
            self.assertIn(part, text)
        self.assertNotIn("<p>", text)

    def test_workable_missing_parts(self):
        self.fake(json.dumps({"description": "<p>Only this.</p>"}))
        self.assertIn("Only this.", company_poller.page_text({"link": WORKABLE}))

    def test_other_sites_unchanged(self):
        self.fake("<html><style>a{}</style><p>Plain page text</p></html>")
        link = "https://jobs.example.com/123"
        text = company_poller.page_text({"link": link})
        self.assertEqual(self.asked, [link])
        self.assertIn("Plain page text", text)
        self.assertNotIn("a{}", text)


if __name__ == "__main__":
    unittest.main()
