import unittest
from unittest.mock import Mock, patch

from openai import RateLimitError

from services.document_extraction import _merge_fields, _response_json, _source_content_type, _vision_input, repair_document_extraction


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


if __name__ == "__main__":
    unittest.main()
