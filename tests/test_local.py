#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local API tests for the 360° virtual tours server.

Usage:
    python test_local.py --verbose --report json

Make sure the server is running, e.g.:
    python server.py --host localhost --port 3000
"""

from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

DEFAULT_BASE_URL = "http://127.0.0.1:3000"
REPORT_DIR = Path(__file__).resolve().parent / "tests"


class TestResult:
    def __init__(self, name: str) -> None:
        self.name = name
        self.passed = False
        self.error = ""
        self.duration_ms = 0.0


_base_url = DEFAULT_BASE_URL


def api_request(method: str, path: str, data: Any = None,
                headers: Dict[str, str] = None) -> Tuple[int, Any]:
    url = f"{_base_url}{path}"
    req_headers = headers or {}
    body = None
    if data is not None and isinstance(data, dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    elif data is not None and isinstance(data, bytes):
        body = data
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8")
        try:
            parsed = json.loads(payload)
        except Exception:
            parsed = {"raw": payload}
        return exc.code, parsed


def multipart_request(path: str, fields: Dict[str, Any], files: Dict[str, Tuple[str, bytes]]) -> Tuple[int, Any]:
    boundary = f"----FormBoundary{int(time.time() * 1000)}"
    body_parts = []
    for name, value in fields.items():
        body_parts.append(f"--{boundary}".encode())
        body_parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        body_parts.append(b"")
        body_parts.append(str(value).encode())
    for name, (filename, data) in files.items():
        body_parts.append(f"--{boundary}".encode())
        body_parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode()
        )
        ext = os.path.splitext(filename)[1].lower()
        content_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
        }.get(ext, "application/octet-stream")
        body_parts.append(f"Content-Type: {content_type}".encode())
        body_parts.append(b"")
        body_parts.append(data)
    body_parts.append(f"--{boundary}--".encode())
    body_parts.append(b"")
    body = b"\r\n".join(body_parts)
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    return api_request("POST", path, data=body, headers=headers)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_create_tour() -> TestResult:
    result = TestResult("test_create_tour")
    status, data = api_request("POST", "/api/tours", {"title": "Квартира для тестов"})
    if status == 201 and data.get("success") and data.get("data", {}).get("id"):
        result.passed = True
    else:
        result.error = f"status={status}, data={data}"
    return result


def test_get_tours_list() -> TestResult:
    result = TestResult("test_get_tours_list")
    status, data = api_request("GET", "/api/tours")
    if status == 200 and data.get("success") and isinstance(data.get("data", {}).get("tours"), list):
        result.passed = True
    else:
        result.error = f"status={status}, data={data}"
    return result


def test_upload_photo() -> TestResult:
    result = TestResult("test_upload_photo")
    tour_id = _first_tour_id_from_api()
    if not tour_id:
        result.error = "no tours found"
        return result
    status, data = multipart_request(
        f"/api/tours/{tour_id}/upload-photo",
        {"scene_id": "scene1"},
        {"file": ("photo.jpg", b"\xff\xd8\xff\xe0fake-jpeg-data" * 100)}
    )
    if status == 200 and data.get("success") and data.get("data", {}).get("filename"):
        result.passed = True
    else:
        result.error = f"status={status}, data={data}"
    return result


def test_upload_minimap() -> TestResult:
    result = TestResult("test_upload_minimap")
    tour_id = _first_tour_id_from_api()
    if not tour_id:
        result.error = "no tours found"
        return result
    status, data = multipart_request(
        f"/api/tours/{tour_id}/upload-minimap",
        {},
        {"file": ("minimap.jpg", b"\xff\xd8\xff\xe0fake-minimap" * 100)}
    )
    if status == 200 and data.get("success") and data.get("data", {}).get("filename"):
        result.passed = True
    else:
        result.error = f"status={status}, data={data}"
    return result


def test_get_photos_list() -> TestResult:
    result = TestResult("test_get_photos_list")
    tour_id = _first_tour_id_from_api()
    if not tour_id:
        result.error = "no tours found"
        return result
    status, data = api_request("GET", f"/api/tours/{tour_id}/photos")
    if status == 200 and data.get("success") and isinstance(data.get("data", {}).get("photos"), list):
        result.passed = True
    else:
        result.error = f"status={status}, data={data}"
    return result


def test_update_config() -> TestResult:
    result = TestResult("test_update_config")
    tour_id = _first_tour_id_from_api()
    if not tour_id:
        result.error = "no tours found"
        return result
    config = {
        "title": "Обновленный тестовый тур",
        "default": {"firstScene": "scene1", "sceneFadeDuration": 1000, "autoLoad": True},
        "scenes": {
            "scene1": {
                "title": "Точка 1",
                "type": "equirectangular",
                "panorama": "photos/photo.jpg",
                "hotSpots": []
            }
        },
        "minimap": {"image": "minimap.jpg", "points": []}
    }
    status, data = api_request("PUT", f"/api/tours/{tour_id}/config", config)
    if status == 200 and data.get("success"):
        result.passed = True
    else:
        result.error = f"status={status}, data={data}"
    return result


def test_get_config() -> TestResult:
    result = TestResult("test_get_config")
    tour_id = _first_tour_id_from_api()
    if not tour_id:
        result.error = "no tours found"
        return result
    status, data = api_request("GET", f"/api/tours/{tour_id}/config")
    if status == 200 and data.get("success") and "scenes" in data.get("data", {}):
        result.passed = True
    else:
        result.error = f"status={status}, data={data}"
    return result


def test_delete_photo() -> TestResult:
    result = TestResult("test_delete_photo")
    tour_id = _first_tour_id_from_api()
    if not tour_id:
        result.error = "no tours found"
        return result
    _, photos_data = api_request("GET", f"/api/tours/{tour_id}/photos")
    photos = photos_data.get("data", {}).get("photos", [])
    if not photos:
        result.error = "no photos to delete"
        return result
    filename = photos[0]["filename"]
    status, data = api_request("DELETE", f"/api/tours/{tour_id}/photos/{filename}")
    if status == 200 and data.get("success"):
        result.passed = True
    else:
        result.error = f"status={status}, data={data}"
    return result


def test_delete_minimap() -> TestResult:
    result = TestResult("test_delete_minimap")
    tour_id = _first_tour_id_from_api()
    if not tour_id:
        result.error = "no tours found"
        return result
    status, data = api_request("DELETE", f"/api/tours/{tour_id}/minimap")
    if status in (200, 404):
        result.passed = True
    else:
        result.error = f"status={status}, data={data}"
    return result


def test_get_tour_info() -> TestResult:
    result = TestResult("test_get_tour_info")
    tour_id = _first_tour_id_from_api()
    if not tour_id:
        result.error = "no tours found"
        return result
    status, data = api_request("GET", f"/api/tours/{tour_id}/info")
    if status == 200 and data.get("success") and "sceneCount" in data.get("data", {}):
        result.passed = True
    else:
        result.error = f"status={status}, data={data}"
    return result


def test_delete_tour() -> TestResult:
    result = TestResult("test_delete_tour")
    status, tours_data = api_request("GET", "/api/tours")
    tour_id = _first_tour_id(tours_data)
    if not tour_id:
        result.error = "no tours found"
        return result
    status, data = api_request("DELETE", f"/api/tours/{tour_id}")
    if status == 200 and data.get("success"):
        result.passed = True
    else:
        result.error = f"status={status}, data={data}"
    return result


def test_file_size_limit() -> TestResult:
    result = TestResult("test_file_size_limit")
    tour_id = _first_tour_id_from_api()
    if not tour_id:
        status, create_data = api_request("POST", "/api/tours", {"title": "Big File Tour"})
        tour_id = create_data.get("data", {}).get("id")
    if not tour_id:
        result.error = "no tours found"
        return result
    # Send data that exceeds the 100MB limit
    big_data = b"\xff\xd8\xff\xe0" + b"x" * (105 * 1024 * 1024)
    status, data = multipart_request(
        f"/api/tours/{tour_id}/upload-photo",
        {"scene_id": "scene1"},
        {"file": ("big.jpg", big_data)}
    )
    if status == 413 and not data.get("success"):
        result.passed = True
    else:
        result.error = f"status={status}, data={data}"
    return result


def test_invalid_extension() -> TestResult:
    result = TestResult("test_invalid_extension")
    tour_id = _first_tour_id_from_api()
    if not tour_id:
        result.error = "no tours found"
        return result
    status, data = multipart_request(
        f"/api/tours/{tour_id}/upload-photo",
        {"scene_id": "scene1"},
        {"file": ("virus.exe", b"MZ fake executable")}
    )
    if status == 400 and not data.get("success"):
        result.passed = True
    else:
        result.error = f"status={status}, data={data}"
    return result


def test_concurrent_uploads() -> TestResult:
    result = TestResult("test_concurrent_uploads")
    tour_id = _first_tour_id_from_api()
    if not tour_id:
        result.error = "no tours found"
        return result

    def upload_one(idx: int) -> Tuple[int, Any]:
        return multipart_request(
            f"/api/tours/{tour_id}/upload-photo",
            {"scene_id": f"scene{idx}"},
            {"file": (f"concurrent{idx}.jpg", b"\xff\xd8\xff\xe0data" * 50)}
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(upload_one, i) for i in range(5)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    ok = all(status == 200 and data.get("success") for status, data in results)
    if ok:
        result.passed = True
    else:
        result.error = f"some uploads failed: {results}"
    return result


def test_security_path_traversal() -> TestResult:
    result = TestResult("test_security_path_traversal")
    # Create a dedicated tour for security tests to avoid ordering issues
    status, create_data = api_request("POST", "/api/tours", {"title": "Security Test"})
    tour_id = create_data.get("data", {}).get("id")
    if not tour_id:
        result.error = "could not create security test tour"
        return result
    # Use URL-encoded path traversal to attempt to access a file outside the tour directory
    status, data = api_request("DELETE", f"/api/tours/{tour_id}/photos/..%2F..%2F..%2Fetc%2Fpasswd")
    if status in (403, 400, 404) and not data.get("success"):
        result.passed = True
    else:
        result.error = f"status={status}, data={data}"
    return result


def test_invalid_tour_id() -> TestResult:
    result = TestResult("test_invalid_tour_id")
    # Test with path traversal attempt
    status, data = api_request("GET", "/api/tours/invalid%2Fpath")
    if status in (400, 403, 404) and not data.get("success"):
        result.passed = True
    else:
        result.error = f"status={status}, data={data}"
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_tour_id(data: Dict[str, Any]) -> str:
    tours = data.get("data", {}).get("tours", [])
    if tours:
        return tours[0]["id"]
    return ""


def _first_tour_id_from_api() -> str:
    _, data = api_request("GET", "/api/tours")
    return _first_tour_id(data)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_tests(tests: List[Callable[[], TestResult]]) -> List[TestResult]:
    results = []
    for test in tests:
        start = time.time()
        try:
            tr = test()
        except Exception as exc:
            tr = TestResult(test.__name__)
            tr.error = str(exc)
        tr.duration_ms = round((time.time() - start) * 1000, 2)
        results.append(tr)
    return results


def print_report(results: List[TestResult], verbose: bool) -> None:
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    for r in results:
        icon = "[OK]" if r.passed else "[FAIL]"
        if verbose or not r.passed:
            print(f"{icon} {r.name} - {'PASSED' if r.passed else 'FAILED'}{(' : ' + r.error) if r.error else ''} ({r.duration_ms}ms)")
    print("-" * 40)
    print(f"TOTAL: {len(results)} tests, {passed} passed, {failed} failed")


def save_json_report(results: List[TestResult]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "tests": [
            {
                "name": r.name,
                "passed": r.passed,
                "error": r.error,
                "duration_ms": r.duration_ms,
            }
            for r in results
        ],
    }
    path = REPORT_DIR / "report.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Report saved to {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local API tests")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Server base URL")
    parser.add_argument("--verbose", action="store_true", help="Print all tests")
    parser.add_argument("--report", choices=["json", "none"], default="none", help="Save JSON report")
    args = parser.parse_args()

    global _base_url
    _base_url = args.base_url

    tests = [
        test_create_tour,
        test_get_tours_list,
        test_upload_photo,
        test_upload_minimap,
        test_get_photos_list,
        test_update_config,
        test_get_config,
        test_delete_photo,
        test_delete_minimap,
        test_get_tour_info,
        test_delete_tour,
        test_security_path_traversal,
        test_invalid_tour_id,
        test_file_size_limit,
        test_invalid_extension,
        test_concurrent_uploads,
    ]

    results = run_tests(tests)
    print_report(results, args.verbose)
    if args.report == "json":
        save_json_report(results)
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
