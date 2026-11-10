import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from ucsb_client import (  # noqa: E402
    UCSBAPIError,
    UCSBCurriculumClient,
    UCSBResponseError,
)


FIXTURE_PATH = ROOT / "tests" / "fixtures" / "ucsb_course_page.json"


class FakeResponse:
    def __init__(self, payload, status=200):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, str):
            payload = payload.encode("utf-8")
        self._body = payload
        self.status = status
        self.closed = False

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def close(self):
        self.closed = True


class FakeOpener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def open(self, api_request, timeout):
        self.calls.append((api_request, timeout))
        if not self.outcomes:
            raise AssertionError("FakeOpener received an unexpected request")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def page_payload(page_number, total, course_ids, page_size=2, quarter="20271"):
    return {
        "pageNumber": page_number,
        "pageSize": page_size,
        "total": total,
        "classes": [
            {"quarter": quarter, "courseId": course_id}
            for course_id in course_ids
        ],
    }


class TestFetchPage(unittest.TestCase):
    def test_fetch_page_encodes_query_and_sends_api_key(self):
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        response = FakeResponse(payload)
        opener = FakeOpener([response])
        client = UCSBCurriculumClient(
            "test-consumer-key",
            timeout=12,
            max_retries=0,
            opener=opener,
        )

        result = client.fetch_page("20271", page_size=1)

        self.assertEqual(result["total"], 1)
        self.assertTrue(response.closed)
        self.assertEqual(len(opener.calls), 1)

        api_request, timeout = opener.calls[0]
        query = parse_qs(urlparse(api_request.full_url).query)
        headers = {key.lower(): value for key, value in api_request.header_items()}

        self.assertEqual(query["quarter"], ["20271"])
        self.assertEqual(query["pageNumber"], ["1"])
        self.assertEqual(query["pageSize"], ["1"])
        self.assertEqual(query["includeClassSections"], ["true"])
        self.assertEqual(headers["ucsb-api-key"], "test-consumer-key")
        self.assertEqual(headers["accept"], "application/json")
        self.assertEqual(timeout, 12)

    def test_fetch_page_can_omit_class_sections(self):
        opener = FakeOpener([FakeResponse(page_payload(1, 0, [], page_size=5))])
        client = UCSBCurriculumClient("key", max_retries=0, opener=opener)

        client.fetch_page("20271", page_size=5, include_class_sections=False)

        query = parse_qs(urlparse(opener.calls[0][0].full_url).query)
        self.assertEqual(query["includeClassSections"], ["false"])

    @patch("ucsb_client.time.sleep")
    def test_transient_network_failure_is_retried(self, sleep):
        opener = FakeOpener(
            [
                URLError("temporary outage"),
                FakeResponse(page_payload(1, 0, [], page_size=100)),
            ]
        )
        client = UCSBCurriculumClient("key", max_retries=1, opener=opener)

        result = client.fetch_page("20271")

        self.assertEqual(result["classes"], [])
        self.assertEqual(len(opener.calls), 2)
        sleep.assert_called_once()

    def test_non_transient_http_failure_is_not_retried(self):
        http_error = HTTPError(
            "https://api.ucsb.edu/example",
            401,
            "Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"message":"invalid key"}'),
        )
        opener = FakeOpener([http_error])
        client = UCSBCurriculumClient("key", max_retries=3, opener=opener)

        with self.assertRaisesRegex(UCSBAPIError, "HTTP 401") as raised:
            client.fetch_page("20271")

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(len(opener.calls), 1)

    def test_invalid_json_is_rejected(self):
        opener = FakeOpener([FakeResponse("not-json")])
        client = UCSBCurriculumClient("key", max_retries=0, opener=opener)

        with self.assertRaisesRegex(UCSBResponseError, "invalid JSON"):
            client.fetch_page("20271")

    def test_invalid_response_metadata_is_rejected(self):
        payload = page_payload(2, 0, [], page_size=100)
        opener = FakeOpener([FakeResponse(payload)])
        client = UCSBCurriculumClient("key", max_retries=0, opener=opener)

        with self.assertRaisesRegex(UCSBResponseError, "returned page 2"):
            client.fetch_page("20271", page_number=1)

    def test_request_arguments_are_validated(self):
        client = UCSBCurriculumClient("key", opener=FakeOpener([]))

        for invalid_quarter in ("", "2027", "winter-2027", True):
            with self.subTest(quarter=invalid_quarter):
                with self.assertRaises((TypeError, ValueError)):
                    client.fetch_page(invalid_quarter)

        with self.assertRaises(ValueError):
            client.fetch_page("20271", page_number=0)
        with self.assertRaises(ValueError):
            client.fetch_page("20271", page_size=0)
        with self.assertRaises(TypeError):
            client.fetch_page("20271", include_class_sections="true")


class TestFetchAllClasses(unittest.TestCase):
    def test_fetch_all_classes_paginates_to_reported_total(self):
        opener = FakeOpener(
            [
                FakeResponse(page_payload(1, 3, ["ANTH      1  ", "CMPSC    16"])),
                FakeResponse(page_payload(2, 3, ["MATH      4A"])),
            ]
        )
        client = UCSBCurriculumClient("key", max_retries=0, opener=opener)

        result = client.fetch_all_classes("20271", page_size=2)

        self.assertEqual(result["quarter"], "20271")
        self.assertEqual(result["expected_total"], 3)
        self.assertEqual(result["fetched_total"], 3)
        self.assertEqual(result["source_total_shortfall"], 0)
        self.assertTrue(result["pagination_complete"])
        self.assertEqual(len(result["classes"]), 3)
        self.assertEqual(len(opener.calls), 2)

    def test_short_records_across_pages_are_reported_as_source_shortfall(self):
        opener = FakeOpener(
            [
                FakeResponse(page_payload(1, 10, ["COURSE 1", "COURSE 2"], 3)),
                FakeResponse(
                    page_payload(2, 10, ["COURSE 3", "COURSE 4", "COURSE 5"], 3)
                ),
                FakeResponse(page_payload(3, 10, ["COURSE 6", "COURSE 7"], 3)),
                FakeResponse(page_payload(4, 10, ["COURSE 8"], 3)),
            ]
        )
        client = UCSBCurriculumClient("key", max_retries=0, opener=opener)

        result = client.fetch_all_classes("20271", page_size=3)

        self.assertEqual(result["expected_total"], 10)
        self.assertEqual(result["fetched_total"], 8)
        self.assertEqual(result["source_total_shortfall"], 2)
        self.assertTrue(result["pagination_complete"])
        self.assertEqual(len(result["classes"]), 8)
        self.assertEqual(len(opener.calls), 4)

    def test_duplicate_course_rejects_repeated_page(self):
        opener = FakeOpener(
            [
                FakeResponse(page_payload(1, 2, ["ANTH      1"], page_size=1)),
                FakeResponse(page_payload(2, 2, ["ANTH 1"], page_size=1)),
            ]
        )
        client = UCSBCurriculumClient("key", max_retries=0, opener=opener)

        with self.assertRaisesRegex(UCSBResponseError, "Duplicate courseId"):
            client.fetch_all_classes("20271", page_size=1)

    def test_empty_page_before_total_is_rejected_as_stalled(self):
        opener = FakeOpener(
            [
                FakeResponse(page_payload(1, 3, ["ANTH 1"], page_size=1)),
                FakeResponse(page_payload(2, 3, [], page_size=1)),
            ]
        )
        client = UCSBCurriculumClient("key", max_retries=0, opener=opener)

        with self.assertRaisesRegex(UCSBResponseError, "stalled"):
            client.fetch_all_classes("20271", page_size=1)

    def test_empty_final_advertised_page_is_reported_as_shortfall(self):
        opener = FakeOpener(
            [
                FakeResponse(page_payload(1, 3, ["ANTH 1", "CMPSC 16"])),
                FakeResponse(page_payload(2, 3, [])),
            ]
        )
        client = UCSBCurriculumClient("key", max_retries=0, opener=opener)

        result = client.fetch_all_classes("20271", page_size=2)

        self.assertEqual(result["fetched_total"], 2)
        self.assertEqual(result["source_total_shortfall"], 1)
        self.assertTrue(result["pagination_complete"])
        self.assertEqual(len(opener.calls), 2)

    def test_changed_total_is_rejected(self):
        opener = FakeOpener(
            [
                FakeResponse(page_payload(1, 3, ["ANTH 1"], page_size=1)),
                FakeResponse(page_payload(2, 4, ["CMPSC 16"], page_size=1)),
            ]
        )
        client = UCSBCurriculumClient("key", max_retries=0, opener=opener)

        with self.assertRaisesRegex(UCSBResponseError, "total changed"):
            client.fetch_all_classes("20271", page_size=1)

    def test_course_from_another_quarter_is_rejected(self):
        opener = FakeOpener(
            [FakeResponse(page_payload(1, 1, ["ANTH 1"], quarter="20272"))]
        )
        client = UCSBCurriculumClient("key", max_retries=0, opener=opener)

        with self.assertRaisesRegex(UCSBResponseError, "expected '20271'"):
            client.fetch_all_classes("20271")


if __name__ == "__main__":
    unittest.main()
