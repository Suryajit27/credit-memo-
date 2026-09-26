import unittest

from services.document_validation import validate_document_extraction


def field(value, status="candidate"):
    return {
        "value": value,
        "status": status,
        "sources": [{"page": 2, "evidence": "Source evidence"}],
    }


class DocumentValidationTests(unittest.TestCase):
    def test_normalizes_ssn_and_date_before_comparing(self):
        extraction = {
            "fields": {
                "Borrower Name": field("michael turner"),
                "Social Security Number": field("987654321"),
                "Date of Birth": field("1954-04-12"),
            }
        }
        record = {
            "extracted_data": {
                "credit_report": {
                    "borrower_name": "Michael Turner",
                    "social_security_number": "987-65-4321",
                    "date_of_birth": "04/12/1954",
                }
            }
        }

        result = validate_document_extraction(extraction, "CreditReport", record)

        checks = {check["fieldName"]: check for check in result["checks"]}
        self.assertEqual(checks["Borrower Name"]["status"], "passed")
        self.assertEqual(checks["Social Security Number"]["status"], "passed")
        self.assertEqual(checks["Date of Birth"]["status"], "passed")
        self.assertEqual(checks["Credit Score"]["status"], "missing_expected_value")
        self.assertEqual(result["status"], "failed")

    def test_marks_mismatch_and_conflict_for_review(self):
        extraction = {
            "fields": {
                "Borrower Name": field("Different borrower"),
                "Social Security Number": field(None, "conflict"),
            }
        }
        record = {
            "extracted_data": {
                "credit_report": {
                    "borrower_name": "Michael Turner",
                    "social_security_number": "987-65-4321",
                }
            }
        }

        result = validate_document_extraction(extraction, "Credit Report", record)

        checks = {check["fieldName"]: check for check in result["checks"]}
        self.assertEqual(checks["Borrower Name"]["status"], "failed")
        self.assertLess(checks["Borrower Name"]["similarityPercent"], 100)
        self.assertEqual(checks["Social Security Number"]["status"], "review_required")
        self.assertEqual(result["status"], "review_required")

    def test_reports_missing_cosmos_source_group(self):
        result = validate_document_extraction({"fields": {}}, "AppraisalReport", {"extracted_data": {}})

        self.assertEqual(result, {"status": "missing_source_data", "sourceGroup": "appraisal_report", "checks": []})

    def test_compares_lists_of_nested_objects_without_sorting_error(self):
        extraction = {"fields": {"Floor Plan Summary": field([{"plan": "Unit A"}, {"plan": "Unit B"}])}}
        record = {"extracted_data": {"appraisal_report": {"floor_plan_summary": [{"plan": "Unit B"}, {"plan": "Unit A"}]}}}

        result = validate_document_extraction(extraction, "AppraisalReport", record)

        checks = {check["fieldName"]: check for check in result["checks"]}
        self.assertEqual(checks["Floor Plan Summary"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
