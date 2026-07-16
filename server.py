#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
360° Virtual Tours Server
=========================
A minimal Python 3.8+ HTTP server for managing virtual tours via a browser.

No external dependencies are required (uses only the Python standard library).
Run:
    python server.py --host 0.0.0.0 --port 3000 --tours-dir ./tours
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import traceback
import zipfile
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

TOUR_ID_RE = re.compile(r"^[a-zA-Z0-9_\-а-яёА-ЯЁ]+$")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.\-а-яёА-ЯЁ]+")
SLUG_RE = re.compile(r"[^a-z0-9а-яё]+", re.IGNORECASE)

MAX_FILE_SIZE_DEFAULT = 100 * 1024 * 1024  # 100 MB
ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif"}
ALLOWED_MINIMAP_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_response(success: bool, data: Any = None, message: str = "",
                  error: str = "", details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    resp: Dict[str, Any] = {"success": success}
    if data is not None:
        resp["data"] = data
    if message:
        resp["message"] = message
    if error:
        resp["error"] = error
    if details:
        resp["details"] = details
    return resp


def is_safe_path(base_dir: Path, target: Path) -> bool:
    """Return True if target is inside base_dir and contains no path traversal."""
    try:
        target.resolve().relative_to(base_dir.resolve())
        return True
    except ValueError:
        return False


def valid_tour_id(tour_id: str) -> bool:
    return bool(TOUR_ID_RE.match(tour_id))


def slugify(text: str) -> str:
    text = (text or "").lower().strip()
    text = SLUG_RE.sub("-", text)
    text = text.strip("-")
    if not text:
        return "tour"
    trans = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    out = "".join(trans.get(ch, ch) for ch in text)
    out = SLUG_RE.sub("-", out).strip("-")
    if not out:
        return "tour"
    return out


def safe_filename(name: str) -> str:
    name = (name or "").strip()
    name = SAFE_NAME_RE.sub("-", name)
    if not name or name in {".", ".."}:
        name = "file"
    return name


def get_extension(filename: str) -> str:
    return os.path.splitext(filename)[1].lower()


def check_disk_space(path: Path, required_bytes: int = 0) -> bool:
    try:
        free = shutil.disk_usage(path).free
        return free > required_bytes + (10 * 1024 * 1024)  # keep 10 MB buffer
    except Exception:
        return True


def parse_multipart_body(content_type: str, body: bytes, max_content_length: int = 100 * 1024 * 1024) -> Dict[str, Dict[str, Any]]:
    """Minimal RFC-7578 multipart/form-data parser with size limits.
    Returns {field_name: {"filename": str|None, "value": bytes|str, "data": bytes}}.
    """
    if "boundary=" not in content_type:
        raise ValueError("boundary missing")
    boundary = content_type.split("boundary=", 1)[1].strip('"')
    delimiter = ("--" + boundary).encode()
    
    if len(body) > max_content_length:
        raise ValueError(f"Multipart body too large: {len(body)} > {max_content_length}")
    
    parts = body.split(delimiter)
    result: Dict[str, Dict[str, Any]] = {}
    for part in parts:
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        headers_bytes, data = part.split(b"\r\n\r\n", 1)
        headers = headers_bytes.decode("utf-8", errors="ignore").split("\r\n")
        disp = {}
        for h in headers:
            if h.lower().startswith("content-disposition"):
                disp = _parse_content_disposition(h)
                break
        name = disp.get("name")
        if not name:
            continue
        filename = disp.get("filename")
        value = data.rstrip(b"\r\n")
        result[name] = {
            "filename": filename,
            "value": value,
            "data": value,
        }
    return result


def _parse_content_disposition(header: str) -> Dict[str, str]:
    result = {}
    parts = header.split(";")
    for p in parts[1:]:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"')
        result[k] = v
    return result


# ---------------------------------------------------------------------------
# JSON logger
# ---------------------------------------------------------------------------

class JsonLogger:
    def __init__(self, log_dir: Path, level: str = "INFO") -> None:
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "server.log"
        numeric = getattr(logging, level.upper(), logging.INFO)
        self.level = numeric
        self._logger = logging.getLogger("virtual-tours")
        self._logger.setLevel(numeric)
        if not self._logger.handlers:
            handler = logging.FileHandler(self.log_file, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)

    def _write(self, level: int, event: str, **kwargs: Any) -> None:
        if level < self.level:
            return
        record = {
            "timestamp": now_iso(),
            "level": logging.getLevelName(level),
            "event": event,
        }
        record.update(kwargs)
        try:
            self._logger.log(level, json.dumps(record, ensure_ascii=False))
        except Exception:
            pass

    def info(self, event: str, **kwargs: Any) -> None:
        self._write(logging.INFO, event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._write(logging.WARNING, event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._write(logging.ERROR, event, **kwargs)

    def debug(self, event: str, **kwargs: Any) -> None:
        self._write(logging.DEBUG, event, **kwargs)


# ---------------------------------------------------------------------------
# Tour manager
# ---------------------------------------------------------------------------

class TourManager:
    def __init__(self, base_dir: Path, tours_dir: Path, public_dir: Path,
                 max_file_size: int = MAX_FILE_SIZE_DEFAULT,
                 logger: Optional[JsonLogger] = None) -> None:
        self.base_dir = base_dir.resolve()
        self.tours_dir = tours_dir.resolve()
        self.public_dir = public_dir.resolve()
        self.max_file_size = max_file_size
        self.logger = logger

        self.tours_dir.mkdir(parents=True, exist_ok=True)
        self.public_dir.mkdir(parents=True, exist_ok=True)

    # -- internal paths ------------------------------------------------------

    def _tour_dir(self, tour_id: str) -> Path:
        if not valid_tour_id(tour_id):
            raise ValueError("INVALID_TOUR_ID")
        return self.tours_dir / f"tour-{tour_id}"

    def _config_path(self, tour_id: str) -> Path:
        return self._tour_dir(tour_id) / "config.json"

    def _photos_dir(self, tour_id: str) -> Path:
        return self._tour_dir(tour_id) / "photos"

    def _minimap_path(self, tour_id: str) -> Path:
        return self._tour_dir(tour_id) / "minimap.jpg"

    # -- config validation ---------------------------------------------------

    def _validate_config(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        if not isinstance(config, dict):
            return False, "config must be an object"
        if "scenes" not in config or not isinstance(config["scenes"], dict):
            return False, "config must contain 'scenes' object"
        for sid, scene in config["scenes"].items():
            if not isinstance(scene, dict):
                return False, f"scene '{sid}' must be an object"
            if not scene.get("panorama"):
                return False, f"scene '{sid}' must have 'panorama'"
        return True, ""

    # -- CRUD ----------------------------------------------------------------

    def list_tours(self) -> List[Dict[str, Any]]:
        result = []
        if not self.tours_dir.exists():
            return result
        for entry in sorted(self.tours_dir.iterdir()):
            if not entry.is_dir() or not entry.name.startswith("tour-"):
                continue
            tour_id = entry.name[len("tour-"):]
            if not valid_tour_id(tour_id):
                continue
            info = self._tour_info(tour_id)
            result.append({
                "id": tour_id,
                "title": info.get("title", tour_id),
                "cover": info.get("cover"),
                "createdAt": info.get("createdAt"),
                "updatedAt": info.get("updatedAt"),
                "sceneCount": info.get("sceneCount", 0),
            })
        return result

    def _read_config(self, tour_id: str) -> Optional[Dict[str, Any]]:
        path = self._config_path(tour_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _tour_info(self, tour_id: str) -> Dict[str, Any]:
        tour_dir = self._tour_dir(tour_id)
        config = self._read_config(tour_id) or {}
        created_at = now_iso()
        updated_at = now_iso()
        try:
            created_at = datetime.fromtimestamp(tour_dir.stat().st_ctime, tz=timezone.utc).isoformat()
            updated_at = datetime.fromtimestamp(tour_dir.stat().st_mtime, tz=timezone.utc).isoformat()
        except Exception:
            pass
        scenes = config.get("scenes", {})
        first_scene_id = config.get("default", {}).get("firstScene") or next(iter(scenes), None)
        cover = None
        if first_scene_id and first_scene_id in scenes:
            cover = scenes[first_scene_id].get("panorama")
        total_size = 0
        if tour_dir.exists():
            for root, _, files in os.walk(tour_dir):
                for f in files:
                    try:
                        total_size += os.path.getsize(os.path.join(root, f))
                    except Exception:
                        pass
        return {
            "id": tour_id,
            "title": config.get("title", tour_id),
            "createdAt": created_at,
            "updatedAt": updated_at,
            "sceneCount": len(scenes),
            "totalSizeBytes": total_size,
            "cover": cover,
        }

    def create_tour(self, title: str) -> Dict[str, Any]:
        base_id = slugify(title)
        tour_id = base_id
        n = 2
        while self._tour_dir(tour_id).exists():
            tour_id = f"{base_id}-{n}"
            n += 1
        tour_dir = self._tour_dir(tour_id)
        photos_dir = self._photos_dir(tour_id)
        tour_dir.mkdir(parents=True, exist_ok=True)
        photos_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "title": title,
            "default": {"firstScene": "", "sceneFadeDuration": 1000, "autoLoad": True},
            "scenes": {},
            "minimap": {"image": "", "points": []},
        }
        self.update_tour_config(tour_id, config)
        if self.logger:
            self.logger.info("tour_created", tour_id=tour_id, title=title)
        return {"id": tour_id, "title": title, "message": "Тур создан"}

    def get_tour_config(self, tour_id: str) -> Dict[str, Any]:
        if not valid_tour_id(tour_id):
            raise ValueError("INVALID_TOUR_ID")
        config = self._read_config(tour_id)
        if config is None:
            raise FileNotFoundError("TOUR_NOT_FOUND")
        return config

    def update_tour_config(self, tour_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        if not valid_tour_id(tour_id):
            raise ValueError("INVALID_TOUR_ID")
        ok, reason = self._validate_config(config)
        if not ok:
            raise ValueError(f"INVALID_CONFIG: {reason}")
        config_path = self._config_path(tour_id)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = config_path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            tmp.replace(config_path)
        except Exception as exc:
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass
            raise RuntimeError(f"INTERNAL_ERROR: {exc}")
        if self.logger:
            self.logger.info("config_updated", tour_id=tour_id, scenes=len(config.get("scenes", {})))
        return {"message": "Конфиг сохранён"}

    def delete_tour(self, tour_id: str) -> Dict[str, Any]:
        if not valid_tour_id(tour_id):
            raise ValueError("INVALID_TOUR_ID")
        tour_dir = self._tour_dir(tour_id)
        if not tour_dir.exists():
            raise FileNotFoundError("TOUR_NOT_FOUND")
        shutil.rmtree(tour_dir)
        if self.logger:
            self.logger.info("tour_deleted", tour_id=tour_id)
        return {"message": "Тур и его файлы удалены"}

    def get_tour_info(self, tour_id: str) -> Dict[str, Any]:
        if not valid_tour_id(tour_id):
            raise ValueError("INVALID_TOUR_ID")
        if not self._tour_dir(tour_id).exists():
            raise FileNotFoundError("TOUR_NOT_FOUND")
        return self._tour_info(tour_id)

    # -- file upload / delete ------------------------------------------------

    def _save_upload(self, tour_id: str, file_field: Dict[str, Any], allowed_ext: set,
                     dest_path: Path, scene_id: Optional[str] = None) -> Dict[str, Any]:
        if not valid_tour_id(tour_id):
            raise ValueError("INVALID_TOUR_ID")
        if file_field is None or not file_field.get("filename"):
            raise ValueError("INVALID_FILE")

        original_name = os.path.basename(file_field["filename"])
        ext = get_extension(original_name)
        if ext not in allowed_ext:
            exts = ", ".join(sorted(allowed_ext))
            raise ValueError(f"Недопустимое расширение файла. Разрешены: {exts}")

        file_size = len(file_field.get("data", b""))
        if file_size == 0:
            raise ValueError("INVALID_FILE")
        if file_size > self.max_file_size:
            raise OSError("FILE_TOO_LARGE")
        if not check_disk_space(dest_path.parent, file_size):
            raise OSError("DISK_FULL")

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
        try:
            with open(tmp_path, "wb") as f:
                f.write(file_field["data"])
            tmp_path.replace(dest_path)
        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            raise

        rel = dest_path.relative_to(self._tour_dir(tour_id))
        result = {
            "filename": dest_path.name,
            "path": str(rel).replace("\\", "/"),
            "size": file_size,
            "url": f"/tours/tour-{tour_id}/{rel.as_posix()}",
        }
        if scene_id:
            result["sceneId"] = scene_id
        if self.logger:
            self.logger.info("file_uploaded", tour_id=tour_id, filename=dest_path.name,
                             size=file_size, scene_id=scene_id)
        return result

    def upload_photo(self, tour_id: str, file_field: Dict[str, Any], scene_id: str) -> Dict[str, Any]:
        if not valid_tour_id(tour_id):
            raise ValueError("INVALID_TOUR_ID")
        if isinstance(scene_id, bytes):
            scene_id = scene_id.decode("utf-8", errors="replace")
        if not scene_id or "/" in scene_id or "\\" in scene_id or scene_id in {".", ".."}:
            raise ValueError("INVALID_FILE")
        original_name = safe_filename(os.path.basename(file_field.get("filename") or "photo.jpg"))
        if not original_name.lower().endswith(tuple(ALLOWED_PHOTO_EXTENSIONS)):
            original_name += ".jpg"
        dest = self._photos_dir(tour_id) / original_name
        return self._save_upload(tour_id, file_field, ALLOWED_PHOTO_EXTENSIONS, dest, scene_id=scene_id)

    def upload_minimap(self, tour_id: str, file_field: Dict[str, Any]) -> Dict[str, Any]:
        if not valid_tour_id(tour_id):
            raise ValueError("INVALID_TOUR_ID")
        dest = self._minimap_path(tour_id)
        return self._save_upload(tour_id, file_field, ALLOWED_MINIMAP_EXTENSIONS, dest)


    def list_photos(self, tour_id: str) -> List[Dict[str, Any]]:
        if not valid_tour_id(tour_id):
            raise ValueError("INVALID_TOUR_ID")
        photos_dir = self._photos_dir(tour_id)
        result = []
        if photos_dir.exists():
            for f in sorted(photos_dir.iterdir()):
                if not f.is_file():
                    continue
                result.append({
                    "filename": f.name,
                    "size": f.stat().st_size,
                    "url": f"/tours/tour-{tour_id}/photos/{f.name}",
                })
        return result

    def delete_photo(self, tour_id: str, filename: str) -> Dict[str, Any]:
        if not valid_tour_id(tour_id):
            raise ValueError("INVALID_TOUR_ID")
        safe = safe_filename(filename)
        path = self._photos_dir(tour_id) / safe
        if not is_safe_path(self.tours_dir, path):
            raise PermissionError("PERMISSION_DENIED")
        if not path.exists():
            raise FileNotFoundError("TOUR_NOT_FOUND")
        path.unlink()
        if self.logger:
            self.logger.info("photo_deleted", tour_id=tour_id, filename=safe)
        return {"message": "Фото удалено"}

    def delete_minimap(self, tour_id: str) -> Dict[str, Any]:
        if not valid_tour_id(tour_id):
            raise ValueError("INVALID_TOUR_ID")
        path = self._minimap_path(tour_id)
        if not path.exists():
            raise FileNotFoundError("TOUR_NOT_FOUND")
        path.unlink()
        if self.logger:
            self.logger.info("minimap_deleted", tour_id=tour_id)
        return {"message": "Миникарта удалена"}

    # -- export --------------------------------------------------------------

    def export_tour_zip(self, tour_id: str, output_path: Path) -> None:
        if not valid_tour_id(tour_id):
            raise ValueError("INVALID_TOUR_ID")
        tour_dir = self._tour_dir(tour_id)
        if not tour_dir.exists():
            raise FileNotFoundError("TOUR_NOT_FOUND")
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(tour_dir):
                for f in files:
                    full = os.path.join(root, f)
                    arc = os.path.relpath(full, tour_dir)
                    zf.write(full, arc)


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class TourHandler(SimpleHTTPRequestHandler):
    server: "TourServer"  # type: ignore

    def log_message(self, format: str, *args: Any) -> None:
        pass

    # -- CORS ----------------------------------------------------------------

    def _send_cors(self) -> None:
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    # -- helpers -------------------------------------------------------------

    def _send_json(self, status: int, data: Dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors()
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, error_code: str, message: str,
                         details: Optional[Dict[str, Any]] = None) -> None:
        self._send_json(status, json_response(False, error=error_code, message=message, details=details))

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _parse_multipart(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/"):
            return None, "INVALID_FILE"
        length = int(self.headers.get("Content-Length", 0))
        
        if length > 10 * 1024 * 1024:
            return None, "INVALID_FILE: multipart body too large"
        
        body = self.rfile.read(length)
        try:
            result = parse_multipart_body(content_type, body)
            return result, None
        except Exception as exc:
            return None, f"INVALID_FILE: {exc}"

    # -- routing -------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        try:
            if path == "/api/tours":
                self._handle_get_tours()
                return
            if path.startswith("/api/tours/"):
                rest = path[len("/api/tours/"):]
                if rest.endswith("/config"):
                    self._handle_get_config(rest[:-len("/config")])
                    return
                if rest.endswith("/info"):
                    self._handle_get_info(rest[:-len("/info")])
                    return
                if rest.endswith("/photos"):
                    self._handle_get_photos(rest[:-len("/photos")])
                    return
                if rest.endswith("/export"):
                    self._handle_export(rest[:-len("/export")])
                    return

            self._serve_static(path)
        except Exception as exc:
            self._handle_exception(exc)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/tours":
                self._handle_create_tour()
                return
            if path.startswith("/api/tours/"):
                rest = path[len("/api/tours/"):]
                if rest.endswith("/upload-photo"):
                    self._handle_upload_photo(rest[:-len("/upload-photo")])
                    return
                if rest.endswith("/upload-minimap"):
                    self._handle_upload_minimap(rest[:-len("/upload-minimap")])
                    return
            self._send_error_json(404, "NOT_FOUND", "Endpoint not found")
        except Exception as exc:
            self._handle_exception(exc)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path.startswith("/api/tours/") and path.endswith("/config"):
                tour_id = path[len("/api/tours/"):-len("/config")]
                self._handle_update_config(tour_id)
                return
            self._send_error_json(404, "NOT_FOUND", "Endpoint not found")
        except Exception as exc:
            self._handle_exception(exc)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path.startswith("/api/tours/"):
                rest = path[len("/api/tours/"):]
                if "/photos/" in rest:
                    tour_id, filename = rest.split("/photos/", 1)
                    self._handle_delete_photo(tour_id, filename)
                    return
                if rest.endswith("/minimap"):
                    self._handle_delete_minimap(rest[:-len("/minimap")])
                    return
                self._handle_delete_tour(rest)
                return
            self._send_error_json(404, "NOT_FOUND", "Endpoint not found")
        except Exception as exc:
            self._handle_exception(exc)

    # -- handlers ------------------------------------------------------------

    def _handle_get_tours(self) -> None:
        tours = self.server.manager.list_tours()
        self._send_json(200, json_response(True, {"tours": tours}, message="Список туров"))

    def _handle_create_tour(self) -> None:
        data = self._read_json_body()
        if data is None or "title" not in data:
            self._send_error_json(400, "INVALID_REQUEST", "Missing 'title'")
            return
        title = str(data["title"]).strip()
        if not title:
            self._send_error_json(400, "INVALID_REQUEST", "Title cannot be empty")
            return
        result = self.server.manager.create_tour(title)
        self._send_json(201, json_response(True, result, message=result.get("message", "")))

    def _handle_get_config(self, tour_id: str) -> None:
        try:
            config = self.server.manager.get_tour_config(tour_id)
            self._send_json(200, json_response(True, config, message="Конфиг тура"))
        except ValueError:
            self._send_error_json(400, "INVALID_TOUR_ID", "Невалидный ID тура")
        except FileNotFoundError:
            self._send_error_json(404, "TOUR_NOT_FOUND", "Тур не найден")

    def _handle_update_config(self, tour_id: str) -> None:
        data = self._read_json_body()
        if data is None:
            self._send_error_json(400, "INVALID_JSON", "Cannot parse JSON body")
            return
        try:
            result = self.server.manager.update_tour_config(tour_id, data)
            self._send_json(200, json_response(True, result, message=result.get("message", "")))
        except ValueError as exc:
            msg = str(exc)
            code = "INVALID_TOUR_ID" if msg.startswith("INVALID_TOUR_ID") else "INVALID_CONFIG"
            self._send_error_json(400, code, msg)
        except FileNotFoundError:
            self._send_error_json(404, "TOUR_NOT_FOUND", "Тур не найден")
        except RuntimeError as exc:
            self._send_error_json(500, "INTERNAL_ERROR", str(exc))

    def _handle_delete_tour(self, tour_id: str) -> None:
        try:
            result = self.server.manager.delete_tour(tour_id)
            self._send_json(200, json_response(True, result, message=result.get("message", "")))
        except ValueError:
            self._send_error_json(400, "INVALID_TOUR_ID", "Невалидный ID тура")
        except FileNotFoundError:
            self._send_error_json(404, "TOUR_NOT_FOUND", "Тур не найден")

    def _handle_get_info(self, tour_id: str) -> None:
        try:
            info = self.server.manager.get_tour_info(tour_id)
            self._send_json(200, json_response(True, info, message="Информация о туре"))
        except ValueError:
            self._send_error_json(400, "INVALID_TOUR_ID", "Невалидный ID тура")
        except FileNotFoundError:
            self._send_error_json(404, "TOUR_NOT_FOUND", "Тур не найден")

    def _handle_upload_photo(self, tour_id: str) -> None:
        parts, err = self._parse_multipart()
        if parts is None:
            self._send_error_json(400, "INVALID_FILE", err or "Cannot parse multipart")
            return
        file_field = parts.get("file")
        scene_id_raw = parts.get("scene_id", {}).get("value")
        if isinstance(scene_id_raw, bytes):
            scene_id = scene_id_raw.decode("utf-8", errors="replace").strip()
        else:
            scene_id = str(scene_id_raw or "scene1").strip()
        try:
            result = self.server.manager.upload_photo(tour_id, file_field, scene_id)
            self._send_json(200, json_response(True, result, message="Фото загружено"))
        except ValueError as exc:
            msg = str(exc)
            code = "INVALID_TOUR_ID" if "INVALID_TOUR_ID" in msg else "INVALID_FILE"
            self._send_error_json(400, code, msg)
        except FileNotFoundError:
            self._send_error_json(404, "TOUR_NOT_FOUND", "Тур не найден")
        except OSError as exc:
            msg = str(exc)
            if "FILE_TOO_LARGE" in msg:
                self._send_error_json(413, "FILE_TOO_LARGE", f"Файл больше {self.server.manager.max_file_size} байт")
            elif "DISK_FULL" in msg:
                self._send_error_json(507, "DISK_FULL", "Недостаточно места на диске")
            else:
                self._send_error_json(500, "INTERNAL_ERROR", msg)
        except RuntimeError as exc:
            self._send_error_json(500, "INTERNAL_ERROR", str(exc))

    def _handle_upload_minimap(self, tour_id: str) -> None:
        parts, err = self._parse_multipart()
        if parts is None:
            self._send_error_json(400, "INVALID_FILE", err or "Cannot parse multipart")
            return
        file_field = parts.get("file")
        try:
            result = self.server.manager.upload_minimap(tour_id, file_field)
            self._send_json(200, json_response(True, result, message="Миникарта загружена"))
        except ValueError as exc:
            msg = str(exc)
            code = "INVALID_TOUR_ID" if "INVALID_TOUR_ID" in msg else "INVALID_FILE"
            self._send_error_json(400, code, msg)
        except FileNotFoundError:
            self._send_error_json(404, "TOUR_NOT_FOUND", "Тур не найден")
        except OSError as exc:
            msg = str(exc)
            if "FILE_TOO_LARGE" in msg:
                self._send_error_json(413, "FILE_TOO_LARGE", f"Файл больше {self.server.manager.max_file_size} байт")
            elif "DISK_FULL" in msg:
                self._send_error_json(507, "DISK_FULL", "Недостаточно места на диске")
            else:
                self._send_error_json(500, "INTERNAL_ERROR", msg)
        except RuntimeError as exc:
            self._send_error_json(500, "INTERNAL_ERROR", str(exc))

    def _handle_get_photos(self, tour_id: str) -> None:
        try:
            photos = self.server.manager.list_photos(tour_id)
            self._send_json(200, json_response(True, {"photos": photos}, message="Список фото"))
        except ValueError:
            self._send_error_json(400, "INVALID_TOUR_ID", "Невалидный ID тура")

    def _handle_delete_photo(self, tour_id: str, filename: str) -> None:
        try:
            result = self.server.manager.delete_photo(tour_id, filename)
            self._send_json(200, json_response(True, result, message=result.get("message", "")))
        except ValueError:
            self._send_error_json(400, "INVALID_TOUR_ID", "Невалидный ID тура")
        except PermissionError:
            self._send_error_json(403, "PERMISSION_DENIED", "Недопустимый путь")
        except FileNotFoundError:
            self._send_error_json(404, "TOUR_NOT_FOUND", "Тур или файл не найден")

    def _handle_delete_minimap(self, tour_id: str) -> None:
        try:
            result = self.server.manager.delete_minimap(tour_id)
            self._send_json(200, json_response(True, result, message=result.get("message", "")))
        except ValueError:
            self._send_error_json(400, "INVALID_TOUR_ID", "Невалидный ID тура")
        except FileNotFoundError:
            self._send_error_json(404, "TOUR_NOT_FOUND", "Миникарта не найдена")

    def _handle_export(self, tour_id: str) -> None:
        try:
            tmp = self.server.manager.tours_dir / f"tour-{tour_id}-export.zip"
            self.server.manager.export_tour_zip(tour_id, tmp)
            self._send_file(tmp, f"{tour_id}.zip", "application/zip")
        except ValueError:
            self._send_error_json(400, "INVALID_TOUR_ID", "Невалидный ID тура")
        except FileNotFoundError:
            self._send_error_json(404, "TOUR_NOT_FOUND", "Тур не найден")

    def _send_file(self, path: Path, download_name: str, mime: str) -> None:
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"attachment; filename=\"{download_name}\"")
        self._send_cors()
        self.end_headers()
        self.wfile.write(data)

    def _handle_exception(self, exc: Exception) -> None:
        exc_type = type(exc).__name__
        if exc_type in {"ValueError", "TypeError", "FileNotFoundError", "PermissionError", "OSError"}:
            pass
        else:
            try:
                if self.server.manager.logger:
                    self.server.manager.logger.error("internal_error", traceback=traceback.format_exc())
            except Exception:
                pass
        self._send_error_json(500, "INTERNAL_ERROR", str(exc))

    # -- static files --------------------------------------------------------

    def _serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"

        safe_path = os.path.normpath(path.lstrip("/"))
        if safe_path.startswith("..") or os.path.isabs(safe_path):
            self._send_error_json(403, "PERMISSION_DENIED", "Недопустимый путь")
            return

        parts = safe_path.replace("\\", "/").split("/", 1)
        if parts[0] == "tours" and len(parts) > 1:
            file_path = self.server.manager.tours_dir / parts[1]
            allowed_root = self.server.manager.tours_dir
        else:
            file_path = self.server.manager.base_dir / safe_path
            allowed_root = self.server.manager.base_dir

        if not is_safe_path(allowed_root, file_path):
            self._send_error_json(403, "PERMISSION_DENIED", "Недопустимый путь")
            return

        if self.command not in ["GET", "HEAD"]:
            self._send_error_json(405, "METHOD_NOT_ALLOWED", "Метод не разрешен")
            return

        if not file_path.exists() or not file_path.is_file():
            self._send_error_json(404, "NOT_FOUND", "Файл не найден")
            return

        ext = os.path.splitext(file_path)[1].lower()
        mime = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".json": "application/json",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }.get(ext, "application/octet-stream")

        with open(file_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self._send_cors()
        self.end_headers()
        self.wfile.write(data)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class TourServer(HTTPServer):
    def __init__(self, address: Tuple[str, int], manager: TourManager) -> None:
        super().__init__(address, TourHandler)
        self.manager = manager


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="360° Virtual Tours Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=3000, help="Port to bind (default: 3000)")
    parser.add_argument("--tours-dir", default="./tours", help="Directory for tour data")
    parser.add_argument("--public-dir", default=".", help="Directory with static files")
    parser.add_argument("--log-dir", default="./logs", help="Directory for log files")
    parser.add_argument("--log-level", default="INFO", help="Log level")
    parser.add_argument("--max-file-size", type=int, default=MAX_FILE_SIZE_DEFAULT, help="Max upload size in bytes")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    base_dir = Path.cwd().resolve()
    tours_dir = (base_dir / args.tours_dir).resolve()
    public_dir = (base_dir / args.public_dir).resolve()
    log_dir = (base_dir / args.log_dir).resolve()

    logger = JsonLogger(log_dir, args.log_level)
    manager = TourManager(base_dir, tours_dir, public_dir, args.max_file_size, logger)

    server = TourServer((args.host, args.port), manager)
    logger.info("server_start", host=args.host, port=args.port, tours_dir=str(tours_dir))
    print(f"Server running on http://{args.host}:{args.port}/")
    print(f"Tours directory: {tours_dir}")
    print(f"Logs: {log_dir / 'server.log'}")
    print("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()
        logger.info("server_stop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
