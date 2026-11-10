"""Regression tests for Cognizant listing discovery (no network required)."""

import unittest
from unittest.mock import patch

import poller


CARD = """
<div class="card card-job" data-id="00071257222">
  <div class="card-body">
    <h3 class="card-title"><a class="stretched-link js-view-job"
      href="/global-en/jobs/00071257222/full-stack-software-developer/">Full Stack Software Developer</a></h3>
    <ul class="list-inline job-meta">
      <li class="list-inline-item">Hartford, Connecticut, United States</li>
      <li class="list-inline-item">Technology &amp; Engineering</li>
    </ul>
  </div>
</div>
"""


class CognizantTests(unittest.TestCase):
    def test_global_us_technology_job_is_found_and_deduplicated(self):
        urls = []

        def fake_fetch(url):
            urls.append(url)
            return CARD if "page=1" in url else ""

        with patch.object(poller, "fetch", side_effect=fake_fetch):
            jobs = poller.cognizant_jobs()

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], "cognizant:00071257222")
        self.assertEqual(jobs[0]["title"], "Full Stack Software Developer")
        self.assertEqual(jobs[0]["location"], "Hartford, Connecticut, United States")
        self.assertEqual(
            jobs[0]["link"],
            "https://careers.cognizant.com/global-en/jobs/00071257222/full-stack-software-developer/",
        )
        self.assertTrue(any("industry=Technology" in url for url in urls))
        self.assertTrue(any("/us-en/jobs/" in url for url in urls))


if __name__ == "__main__":
    unittest.main()
