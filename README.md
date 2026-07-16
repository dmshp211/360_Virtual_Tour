# 360° Virtual Tours

Browser-based editor and viewer for 360° virtual tours. No external dependencies — built on Python stdlib and Pannellum.

Create tours, upload panoramas, add interactive hotspots and minimaps, all from the browser.

## Quick start

```bash
python server.py
```

Open `http://localhost:3000/index.html` to view tours, `http://localhost:3000/editor.html` to edit.

## Features

- Browser-based tour editor with live panorama preview
- Multi-tour viewer with scene navigation and minimap
- File upload via multipart API (photos, minimaps)
- JSON config storage per tour
- Python 3.8+, zero dependencies
