import unittest
from io import BytesIO
from unittest.mock import Mock, patch

from openai import APIStatusError, RateLimitError
from pypdf import PdfReader, PdfWriter

from services.document_extraction import (
    _document_profile,
    _is_oversized_vision_input,
    _merge_fields,
    _response_json,
    _page_batches,
    _remap_batch_pages,
    _selected_pdf,
    _select_pages,
    _source_content_type,
    _vision_input,
    repair_document_extraction,
)


class DocumentExtractionTests(unittest.TestCase):
    def test_infers_image_mime_type_when_blob_metadata_is_generic(self):
        self.assertEqual(
            _source_content_type("request/source.jpg", "application/octet-stream"),
            "image/jpeg",
        )

    def test_vision_input_includes_image_data(self):
        request = _vision_input("image/png", b"image-bytes", "review this")
        content = request[0]["content"]
        self.assertEqual(content[1]["type"], "input_image")
        self.assertTrue(content[1]["image_url"].startswith("data:image/png;base64,"))

    def test_matching_candidates_are_automatically_selected(self):
        raw = {"loanAmount": {"value": 425000, "valueType": "currency", "confidence": 0.91, "page": 1, "evidence": "$425,000"}}
        fields = _merge_fields(raw, raw, "gpt-5.4-mini")
        field = fields["loanAmount"]
        self.assertEqual(field["status"], "candidate")
        self.assertEqual(field["value"], 425000)
        self.assertEqual(len(field["candidates"]), 2)

    def test_conflicting_candidates_require_review(self):
        base = {"loanAmount": {"value": 42500, "confidence": 0.75, "page": 1, "evidence": "$42,500"}}
        vision = {"loanAmount": {"value": 425000, "confidence": 0.97, "page": 1, "evidence": "$425,000"}}
        fields = _merge_fields(base, vision, "gpt-5.4-mini")
        field = fields["loanAmount"]
        self.assertEqual(field["status"], "conflict")
        self.assertIsNone(field["value"])
        self.assertEqual(len(field["candidates"]), 2)

    def test_fields_without_evidence_are_rejected(self):
        base = {"borrower": {"value": "Jane Doe", "confidence": 0.99, "page": 1}}
        self.assertEqual(_merge_fields(base, {}, "gpt-5.4-mini"), {})

    @patch("services.document_extraction.time.sleep")
    def test_retries_rate_limited_responses(self, sleep):
        client = Mock()
        rate_limited = RateLimitError("rate limited", response=Mock(status_code=429, headers={}), body=None)
        client.responses.create.side_effect = [rate_limited, Mock(output_text='{"fields": {}}')]

        response = _response_json(client, "gpt-5.4-mini", "extract", "layout")

        self.assertEqual(response, {"fields": {}})
        self.assertEqual(client.responses.create.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_repaired_vision_json_merges_without_model_call(self):
        base = {"loanAmount": {"value": 42500, "confidence": 0.7, "page": 1, "evidence": "$42,500"}}
        repaired = repair_document_extraction(
            "request/source.jpg",
            "source.jpg",
            "vision",
            '{"fields":{"loanAmount":{"value":425000,"confidence":0.98,"page":1,"evidence":"$425,000"}}}',
            base,
        )
        field = repaired["fields"]["loanAmount"]
        self.assertEqual(field["status"], "conflict")
        self.assertEqual(len(field["candidates"]), 2)

    def test_uses_credit_report_profile_for_classifier_document_type(self):
        profile = _document_profile("CreditReport")

        self.assertIsNotNone(profile)
        self.assertEqual(profile["name"], "Credit Report")
        self.assertIn("Credit Score", [field["fieldName"] for field in profile["extractionFields"]])

    def test_filters_to_matching_pages_and_includes_neighbors(self):
        profile = {
            "name": "Test report",
            "extractionFields": [
                {"fieldName": "Loan Number", "searchTerms": ["Loan Number"]},
            ],
        }
        pages = [
            {"pageNumber": 1, "text": "Cover page", "tables": []},
            {"pageNumber": 2, "text": "Loan Number: LN-123", "tables": []},
            {"pageNumber": 3, "text": "Supporting information", "tables": []},
            {"pageNumber": 4, "text": "Unrelated appendix", "tables": []},
        ]

        selected, details = _select_pages(pages, profile)

        self.assertEqual([page["pageNumber"] for page in selected], [1, 2, 3])
        self.assertEqual(details["mode"], "profile-filtered")
        self.assertEqual(details["selectionReasons"][0]["matchedTerms"], ["Loan Number"])

    def test_rebuilds_pdf_with_only_selected_pages(self):
        writer = PdfWriter()
        for _ in range(4):
            writer.add_blank_page(width=100, height=100)
        source = BytesIO()
        writer.write(source)

        selected = _selected_pdf(source.getvalue(), [{"pageNumber": 2}, {"pageNumber": 4}])

        self.assertEqual(len(PdfReader(BytesIO(selected)).pages), 2)

    def test_keeps_layout_result_when_only_one_vision_page_is_oversized(self):
        base = {"Credit Score": {"value": 652, "confidence": 0.9, "page": 2, "evidence": "FICO 652"}}
        vision = [{"Credit Score": {"value": 652, "confidence": 0.95, "page": 2, "evidence": "FICO 652"}}, {}]

        fields = _merge_fields(base, vision, "gpt-5.4-mini")

        self.assertEqual(fields["Credit Score"]["value"], 652)
        self.assertEqual(len(fields["Credit Score"]["candidates"]), 2)

    def test_identifies_oversized_vision_error(self):
        error = APIStatusError("Invalid request", response=Mock(status_code=400, headers={}), body={"code": "InvalidContentLength", "message": "The input image is too large."})

        self.assertTrue(_is_oversized_vision_input(error))

    def test_batches_vision_pages_in_groups_of_five(self):
        pages = [{"pageNumber": number} for number in range(1, 13)]

        batches = _page_batches(pages)

        self.assertEqual([[page["pageNumber"] for page in batch] for batch in batches], [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10], [11, 12]])

    def test_remaps_layout_batch_page_numbers_to_original_document(self):
        batch_result = [{"pageNumber": 1, "text": "First"}, {"pageNumber": 2, "text": "Second"}]
        original_pages = [{"pageNumber": 6}, {"pageNumber": 7}]

        remapped = _remap_batch_pages(batch_result, original_pages)

        self.assertEqual([page["pageNumber"] for page in remapped], [6, 7])


if __name__ == "__main__":
    unittest.main()
