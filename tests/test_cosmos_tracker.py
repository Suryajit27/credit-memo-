import unittest

from services.cosmos_tracker import _attach_demo_extracted_data, _demo_extracted_data


class CosmosTrackerTests(unittest.TestCase):
    def test_returns_demo_source_data_only_for_the_demo_request(self):
        data = _demo_extracted_data("sample-meridian-foods-2026")

        self.assertEqual(data["credit_report"]["borrower_name"], "Michael Turner")
        self.assertEqual(_demo_extracted_data("IDX-260917-AE2BEFB6"), None)

    def test_attaches_demo_source_data_once(self):
        item = {"requestId": "sample-meridian-foods-2026"}

        self.assertTrue(_attach_demo_extracted_data(item))
        self.assertIn("appraisal_report", item["extracted_data"])
        self.assertFalse(_attach_demo_extracted_data(item))


if __name__ == "__main__":
    unittest.main()
