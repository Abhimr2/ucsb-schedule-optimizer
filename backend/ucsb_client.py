from __future__ import annotations

import json
import socket
import time
from urllib import error, parse, request


DEFAULT_BASE_URL = "https://api.ucsb.edu/academics/curriculums"
TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class UCSBClientError(RuntimeError):
    pass


class UCSBAPIError(UCSBClientError):
    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class UCSBResponseError(UCSBClientError):
    pass


class UCSBCurriculumClient:
    def __init__(
        self,
        api_key,
        base_url=DEFAULT_BASE_URL,
        timeout=30,
        max_retries=3,
        opener=None,
    ):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must be a non-empty string")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout must be a positive number")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int):
            raise TypeError("max_retries must be a non-negative integer")
        if max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        if opener is not None and not callable(opener) and not hasattr(opener, "open"):
            raise TypeError("opener must be callable or provide an open() method")

        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.opener = opener if opener is not None else request.build_opener()

    def fetch_page(
        self,
        quarter,
        page_number=1,
        page_size=100,
        include_class_sections=True,
    ):
        quarter = self._validate_quarter(quarter)
        page_number = self._validate_positive_integer("page_number", page_number)
        page_size = self._validate_positive_integer("page_size", page_size)
        if not isinstance(include_class_sections, bool):
            raise TypeError("include_class_sections must be a boolean")

        query = parse.urlencode(
            {
                "quarter": quarter,
                "includeClassSections": str(include_class_sections).lower(),
                "pageNumber": page_number,
                "pageSize": page_size,
            }
        )
        url = f"{self.base_url}/v3/classes/search?{query}"
        api_request = request.Request(
            url,
            headers={
                "Accept": "application/json",
                "ucsb-api-key": self.api_key,
            },
            method="GET",
        )

        payload = self._request_json(api_request)
        self._validate_page_payload(payload, requested_page=page_number)
        return payload

    def fetch_all_classes(self, quarter, page_size=100):
        quarter = self._validate_quarter(quarter)
        page_size = self._validate_positive_integer("page_size", page_size)

        classes = []
        seen_course_ids = set()
        first_page = self.fetch_page(
            quarter,
            page_number=1,
            page_size=page_size,
            include_class_sections=True,
        )
        expected_total = first_page["total"]
        advertised_page_count = (expected_total + page_size - 1) // page_size

        for page_number in range(1, advertised_page_count + 1):
            if page_number == 1:
                page = first_page
            else:
                page = self.fetch_page(
                    quarter,
                    page_number=page_number,
                    page_size=page_size,
                    include_class_sections=True,
                )

            if page["pageNumber"] != page_number:
                raise UCSBResponseError(
                    f"Expected page {page_number}, received page {page['pageNumber']}"
                )

            if page["total"] != expected_total:
                raise UCSBResponseError(
                    "The API total changed during pagination "
                    f"({expected_total} to {page['total']})"
                )

            page_classes = page["classes"]
            if not page_classes and page_number < advertised_page_count:
                raise UCSBResponseError(
                    f"Pagination stalled at page {page_number}: "
                    "received an empty page before the final advertised page "
                    f"{advertised_page_count}"
                )

            for raw_course in page_classes:
                if not isinstance(raw_course, dict):
                    raise UCSBResponseError(
                        f"Page {page_number} contains a class that is not an object"
                    )

                class_quarter = str(raw_course.get("quarter", "")).strip()
                if class_quarter != quarter:
                    raise UCSBResponseError(
                        f"Course on page {page_number} has quarter "
                        f"{class_quarter!r}; expected {quarter!r}"
                    )

                course_id = self._canonical_course_id(raw_course.get("courseId"))
                if not course_id:
                    raise UCSBResponseError(
                        f"Course on page {page_number} is missing courseId"
                    )
                if course_id in seen_course_ids:
                    raise UCSBResponseError(
                        f"Duplicate courseId {course_id!r} encountered on page "
                        f"{page_number}; pagination may have stalled"
                    )

                seen_course_ids.add(course_id)
                classes.append(raw_course)

            if len(classes) > expected_total:
                raise UCSBResponseError(
                    f"Fetched {len(classes)} classes, exceeding API total "
                    f"{expected_total}"
                )

        fetched_total = len(classes)

        return {
            "quarter": quarter,
            "expected_total": expected_total,
            "fetched_total": fetched_total,
            "source_total_shortfall": expected_total - fetched_total,
            "pagination_complete": True,
            "classes": classes,
        }

    def _request_json(self, api_request):
        for attempt in range(self.max_retries + 1):
            try:
                response = self._open(api_request)
                try:
                    status = self._response_status(response)
                    body = response.read()
                finally:
                    close = getattr(response, "close", None)
                    if close is not None:
                        close()

                if status is not None and not 200 <= status < 300:
                    if status in TRANSIENT_HTTP_STATUSES and attempt < self.max_retries:
                        self._wait_before_retry(attempt)
                        continue
                    raise UCSBAPIError(
                        self._http_error_message(status, body),
                        status_code=status,
                    )

            except error.HTTPError as exc:
                status = exc.code
                body = self._read_error_body(exc)
                if status in TRANSIENT_HTTP_STATUSES and attempt < self.max_retries:
                    self._wait_before_retry(attempt)
                    continue
                raise UCSBAPIError(
                    self._http_error_message(status, body),
                    status_code=status,
                ) from exc
            except (error.URLError, TimeoutError, socket.timeout, OSError) as exc:
                if attempt < self.max_retries:
                    self._wait_before_retry(attempt)
                    continue
                raise UCSBAPIError(
                    f"Unable to reach the UCSB API after {attempt + 1} attempt(s): "
                    f"{exc}"
                ) from exc

            try:
                if isinstance(body, bytes):
                    body = body.decode("utf-8")
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
                raise UCSBResponseError("UCSB returned invalid JSON") from exc

            if not isinstance(payload, dict):
                raise UCSBResponseError("UCSB response must be a JSON object")
            return payload

        raise UCSBAPIError("Retry limit reached without a response")

    def _open(self, api_request):
        if hasattr(self.opener, "open"):
            return self.opener.open(api_request, timeout=self.timeout)
        return self.opener(api_request, timeout=self.timeout)

    @staticmethod
    def _response_status(response):
        status = getattr(response, "status", None)
        if status is None and hasattr(response, "getcode"):
            status = response.getcode()
        return status

    @staticmethod
    def _read_error_body(exc):
        try:
            return exc.read()
        except (AttributeError, OSError):
            return b""
        finally:
            close = getattr(exc, "close", None)
            if close is not None:
                close()

    @staticmethod
    def _http_error_message(status, body):
        detail = ""
        if body:
            if isinstance(body, bytes):
                body = body.decode("utf-8", errors="replace")
            detail = " ".join(str(body).split())[:200]
        message = f"UCSB API request failed with HTTP {status}"
        if detail:
            message += f": {detail}"
        return message

    @staticmethod
    def _wait_before_retry(attempt):
        time.sleep(min(0.25 * (2**attempt), 2.0))

    @classmethod
    def _validate_page_payload(cls, payload, requested_page):
        if not isinstance(payload, dict):
            raise UCSBResponseError("UCSB response must be a JSON object")

        page_number = cls._response_integer(payload, "pageNumber", minimum=1)
        page_size = cls._response_integer(payload, "pageSize", minimum=1)
        total = cls._response_integer(payload, "total", minimum=0)
        classes = payload.get("classes")

        if page_number != requested_page:
            raise UCSBResponseError(
                f"Requested page {requested_page}, but UCSB returned page {page_number}"
            )
        if not isinstance(classes, list):
            raise UCSBResponseError("Response field 'classes' must be a list")
        if len(classes) > page_size:
            raise UCSBResponseError(
                f"Response contains {len(classes)} classes but reports pageSize "
                f"{page_size}"
            )
        if len(classes) > total:
            raise UCSBResponseError(
                f"Response contains {len(classes)} classes but reports total {total}"
            )

    @staticmethod
    def _response_integer(payload, field, minimum):
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise UCSBResponseError(
                f"Response field {field!r} must be an integer >= {minimum}"
            )
        return value

    @staticmethod
    def _validate_quarter(quarter):
        if isinstance(quarter, bool):
            raise TypeError("quarter must be a five-digit UCSB quarter code")
        quarter = str(quarter).strip()
        if len(quarter) != 5 or not quarter.isdigit():
            raise ValueError("quarter must be a five-digit UCSB quarter code")
        return quarter

    @staticmethod
    def _validate_positive_integer(name, value):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be a positive integer")
        if value < 1:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _canonical_course_id(value):
        if value is None:
            return ""
        return " ".join(str(value).split())
