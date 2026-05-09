"""
tools/tools_media.py — Document intelligence, image/video analysis, facial recognition.

Capabilities:
  read_document(path)          — extract text+tables from PDF, DOCX, PPTX, TXT
  analyze_image(path, q)       — Claude vision analysis of any image
  analyze_video(path, q)       — sample video frames → Claude vision
  detect_faces(path)           — DeepFace: detect faces + age/gender/emotion
  recognize_face(path)         — match face against data/faces/ database
  register_face(path, name)    — add image to face database
  ocr_image(path)              — extract text from image via Tesseract / Claude fallback

Face database: data/faces/<Name>/img1.jpg  (gitignored — personal biometric data)
"""

import base64
import io
import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ROOT       = Path(__file__).parent.parent
_FACES_DIR  = _ROOT / "data" / "faces"
_TEMP_DIR   = _ROOT / "data" / "media_temp"

# ── optional imports ──────────────────────────────────────────────────────────

try:
    import pdfplumber
    _PDF_OK = True
except ImportError:
    _PDF_OK = False

try:
    from docx import Document as _DocxDocument
    _DOCX_OK = True
except ImportError:
    _DOCX_OK = False

try:
    from pptx import Presentation as _Pptx
    _PPTX_OK = True
except ImportError:
    _PPTX_OK = False

try:
    from PIL import Image as _PIL
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

try:
    import cv2 as _cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

try:
    import deepface as _df_mod
    from deepface import DeepFace
    _DEEPFACE_OK = True
except ImportError:
    _DEEPFACE_OK = False

try:
    import pytesseract
    _TESS_OK = True
except ImportError:
    _TESS_OK = False

try:
    import anthropic as _anthropic
    _ANTHROPIC_OK = True
except ImportError:
    _ANTHROPIC_OK = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _encode_image(path: str) -> Tuple[str, str]:
    """Return (base64_data, media_type) for an image file."""
    ext = Path(path).suffix.lower()
    mime_map = {
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png":  "image/png",
        ".gif":  "image/gif",
        ".webp": "image/webp",
    }
    media_type = mime_map.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, media_type


def _claude_vision(image_paths: List[str], question: str, max_tokens: int = 2048) -> str:
    """Send one or more images to Claude vision and return the response text."""
    if not _ANTHROPIC_OK:
        return "[vision] anthropic not installed"
    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env")
    except Exception:
        pass

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return "[vision] ANTHROPIC_API_KEY not set"

    client = _anthropic.Anthropic(api_key=key)
    content = []
    for path in image_paths[:5]:  # Claude supports up to 20, but keep reasonable
        try:
            data, mime = _encode_image(path)
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": data},
            })
        except Exception as e:
            content.append({"type": "text", "text": f"[Could not load {path}: {e}]"})

    content.append({"type": "text", "text": question or "Describe this image in detail."})

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
    )
    return msg.content[0].text


def _ensure_dir(d: Path):
    d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# MediaTools
# ---------------------------------------------------------------------------

class MediaTools:
    """Document intelligence + image/video analysis + facial recognition."""

    # ── Document reading ───────────────────────────────────────────────────────

    @staticmethod
    def read_document(path: str, max_chars: int = 50_000) -> Dict:
        """
        Extract text and tables from PDF, DOCX, PPTX, or TXT.
        Returns {"success", "text", "pages", "tables", "metadata"}.
        """
        path = str(path)
        ext  = Path(path).suffix.lower()

        if not Path(path).exists():
            return {"success": False, "error": f"File not found: {path}"}

        try:
            if ext == ".pdf":
                return MediaTools._read_pdf(path, max_chars)
            elif ext in (".docx", ".doc"):
                return MediaTools._read_docx(path, max_chars)
            elif ext in (".pptx", ".ppt"):
                return MediaTools._read_pptx(path, max_chars)
            elif ext in (".txt", ".md", ".csv", ".json", ".py", ".js", ".ts"):
                text = Path(path).read_text(encoding="utf-8", errors="replace")[:max_chars]
                return {"success": True, "text": text, "pages": 1, "tables": [], "metadata": {}}
            else:
                return {"success": False, "error": f"Unsupported file type: {ext}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def _read_pdf(path: str, max_chars: int) -> Dict:
        if not _PDF_OK:
            return {"success": False, "error": "pdfplumber not installed — pip install pdfplumber"}

        pages_text = []
        tables     = []
        metadata   = {}

        with pdfplumber.open(path) as pdf:
            metadata = pdf.metadata or {}
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                pages_text.append(text)
                for tbl in page.extract_tables():
                    if tbl:
                        tables.append({"page": i + 1, "rows": tbl})

        full_text = "\n\n".join(pages_text)
        if len(full_text) > max_chars:
            full_text = full_text[:max_chars] + f"\n... [{len(full_text)-max_chars} chars truncated]"

        return {
            "success":  True,
            "text":     full_text,
            "pages":    len(pages_text),
            "tables":   tables[:20],  # cap table count
            "metadata": {k: str(v) for k, v in metadata.items() if v},
        }

    @staticmethod
    def _read_docx(path: str, max_chars: int) -> Dict:
        if not _DOCX_OK:
            return {"success": False, "error": "python-docx not installed — pip install python-docx"}

        doc   = _DocxDocument(path)
        paras = [p.text for p in doc.paragraphs if p.text.strip()]
        tables = []
        for tbl in doc.tables:
            rows = []
            for row in tbl.rows:
                rows.append([cell.text.strip() for cell in row.cells])
            if rows:
                tables.append({"rows": rows})

        text = "\n".join(paras)
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... truncated"

        return {"success": True, "text": text, "pages": len(paras), "tables": tables, "metadata": {}}

    @staticmethod
    def _read_pptx(path: str, max_chars: int) -> Dict:
        if not _PPTX_OK:
            return {"success": False, "error": "python-pptx not installed — pip install python-pptx"}

        prs    = _Pptx(path)
        slides = []
        for i, slide in enumerate(prs.slides):
            slide_text = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    slide_text.append(shape.text.strip())
            if slide_text:
                slides.append(f"[Slide {i+1}]\n" + "\n".join(slide_text))

        text = "\n\n".join(slides)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... truncated"

        return {
            "success": True,
            "text":    text,
            "pages":   len(prs.slides),
            "tables":  [],
            "metadata": {"slide_count": len(prs.slides)},
        }

    # ── Image analysis ─────────────────────────────────────────────────────────

    @staticmethod
    def analyze_image(path: str, question: str = "") -> Dict:
        """Send an image to Claude vision and return the analysis."""
        if not Path(path).exists():
            return {"success": False, "error": f"File not found: {path}"}

        ext = Path(path).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"):
            return {"success": False, "error": f"Unsupported image format: {ext}"}

        # Convert non-native formats to JPEG first
        actual_path = path
        if ext in (".bmp", ".tiff") and _PIL_OK:
            tmp = str(_TEMP_DIR / f"conv_{Path(path).stem}.jpg")
            _ensure_dir(_TEMP_DIR)
            _PIL.open(path).convert("RGB").save(tmp, "JPEG")
            actual_path = tmp

        try:
            result = _claude_vision([actual_path], question or "Describe this image in detail.")
            return {"success": True, "path": path, "analysis": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def analyze_images(paths: List[str], question: str = "") -> Dict:
        """Analyze multiple images together (compare, find differences, etc.)."""
        existing = [p for p in paths if Path(p).exists()]
        if not existing:
            return {"success": False, "error": "No valid image paths provided"}
        try:
            result = _claude_vision(existing, question or "Analyze these images.")
            return {"success": True, "paths": existing, "analysis": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Video analysis ─────────────────────────────────────────────────────────

    @staticmethod
    def analyze_video(path: str, question: str = "", max_frames: int = 8) -> Dict:
        """
        Sample frames from a video and analyze with Claude vision.
        Samples up to max_frames evenly spaced frames.
        """
        if not _CV2_OK:
            return {"success": False, "error": "opencv-python not installed — pip install opencv-python"}
        if not Path(path).exists():
            return {"success": False, "error": f"File not found: {path}"}

        _ensure_dir(_TEMP_DIR)
        cap = _cv2.VideoCapture(path)
        if not cap.isOpened():
            return {"success": False, "error": "Could not open video file"}

        total_frames = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT))
        fps          = cap.get(_cv2.CAP_PROP_FPS)
        duration_s   = total_frames / fps if fps > 0 else 0

        if total_frames < 1:
            cap.release()
            return {"success": False, "error": "Video has no frames"}

        # Sample evenly
        frame_indices = [
            int(i * total_frames / max_frames)
            for i in range(min(max_frames, total_frames))
        ]

        saved_frames = []
        for idx in frame_indices:
            cap.set(_cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue
            frame_path = str(_TEMP_DIR / f"frame_{idx:06d}.jpg")
            _cv2.imwrite(frame_path, frame)
            saved_frames.append(frame_path)

        cap.release()

        if not saved_frames:
            return {"success": False, "error": "Could not extract frames"}

        try:
            q = question or f"Analyze this video ({duration_s:.1f}s). Describe what's happening across all frames."
            analysis = _claude_vision(saved_frames, q)
            return {
                "success":      True,
                "path":         path,
                "duration_s":   round(duration_s, 1),
                "total_frames": total_frames,
                "sampled":      len(saved_frames),
                "analysis":     analysis,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            # Clean up temp frames
            for fp in saved_frames:
                try:
                    os.remove(fp)
                except Exception:
                    pass

    # ── OCR ───────────────────────────────────────────────────────────────────

    @staticmethod
    def ocr_image(path: str) -> Dict:
        """
        Extract text from an image. Tries Tesseract first, falls back to Claude vision.
        """
        if not Path(path).exists():
            return {"success": False, "error": f"File not found: {path}"}

        if _TESS_OK:
            try:
                img  = _PIL.open(path) if _PIL_OK else None
                text = pytesseract.image_to_string(img or path).strip()
                if text:
                    return {"success": True, "text": text, "engine": "tesseract"}
            except Exception:
                pass

        # Claude vision fallback
        try:
            result = _claude_vision([path], "Extract ALL text from this image exactly as it appears. Return only the text, no commentary.")
            return {"success": True, "text": result, "engine": "claude_vision"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Facial recognition ────────────────────────────────────────────────────

    @staticmethod
    def detect_faces(path: str) -> Dict:
        """
        Detect faces in an image and return attributes (age, gender, emotion, race).
        Uses DeepFace. Does NOT identify who the person is — use recognize_face for that.
        """
        if not _DEEPFACE_OK:
            return {"success": False, "error": "deepface not installed — pip install deepface"}
        if not Path(path).exists():
            return {"success": False, "error": f"File not found: {path}"}

        try:
            results = DeepFace.analyze(
                img_path=path,
                actions=["age", "gender", "emotion", "race"],
                enforce_detection=False,
                silent=True,
            )
            if not isinstance(results, list):
                results = [results]

            faces = []
            for r in results:
                faces.append({
                    "age":           r.get("age"),
                    "gender":        r.get("dominant_gender"),
                    "emotion":       r.get("dominant_emotion"),
                    "race":          r.get("dominant_race"),
                    "region":        r.get("region"),
                    "gender_scores": r.get("gender"),
                    "emotion_scores": r.get("emotion"),
                })

            return {
                "success":    True,
                "face_count": len(faces),
                "faces":      faces,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def register_face(path: str, name: str) -> Dict:
        """
        Register a face in the local database under the given name.
        Saves to data/faces/<name>/ directory (gitignored).
        """
        if not Path(path).exists():
            return {"success": False, "error": f"File not found: {path}"}
        if not name.strip():
            return {"success": False, "error": "Name cannot be empty"}

        safe_name = re.sub(r"[^\w\-]", "_", name.strip())
        face_dir  = _FACES_DIR / safe_name
        face_dir.mkdir(parents=True, exist_ok=True)

        # Count existing images for this person
        existing = list(face_dir.glob("*.jpg")) + list(face_dir.glob("*.png"))
        dest = face_dir / f"face_{len(existing)+1:03d}{Path(path).suffix}"

        try:
            shutil.copy2(path, dest)
            return {
                "success": True,
                "name":    safe_name,
                "saved":   str(dest),
                "total_for_person": len(existing) + 1,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def recognize_face(path: str, threshold: float = 0.4) -> Dict:
        """
        Identify a face in path against the registered face database.
        Returns the best match name + confidence. Needs registered faces via register_face().
        """
        if not _DEEPFACE_OK:
            return {"success": False, "error": "deepface not installed — pip install deepface"}
        if not Path(path).exists():
            return {"success": False, "error": f"File not found: {path}"}
        if not _FACES_DIR.exists() or not any(_FACES_DIR.iterdir()):
            return {"success": False, "error": "No faces registered. Use register_face() first."}

        try:
            results = DeepFace.find(
                img_path=path,
                db_path=str(_FACES_DIR),
                model_name="VGG-Face",
                enforce_detection=False,
                silent=True,
            )
            matches = []
            for df in (results if isinstance(results, list) else [results]):
                if hasattr(df, "iterrows"):
                    for _, row in df.iterrows():
                        identity = str(row.get("identity", ""))
                        # Extract name from path: data/faces/<name>/face_001.jpg
                        parts    = Path(identity).parts
                        try:
                            name_idx = parts.index("faces") + 1
                            name     = parts[name_idx]
                        except (ValueError, IndexError):
                            name = Path(identity).parent.name
                        dist = float(row.get("distance", 1.0))
                        matches.append({"name": name, "distance": round(dist, 4), "path": identity})

            if not matches:
                return {"success": True, "match": None, "message": "No match found in database"}

            best = min(matches, key=lambda x: x["distance"])
            confident = best["distance"] <= threshold

            return {
                "success":    True,
                "match":      best["name"] if confident else None,
                "distance":   best["distance"],
                "confident":  confident,
                "threshold":  threshold,
                "all_matches": matches[:5],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def list_registered_faces() -> Dict:
        """List all registered people in the face database."""
        if not _FACES_DIR.exists():
            return {"success": True, "people": [], "count": 0}
        people = []
        for d in sorted(_FACES_DIR.iterdir()):
            if d.is_dir():
                imgs = len(list(d.glob("*.jpg")) + list(d.glob("*.png")))
                people.append({"name": d.name, "images": imgs})
        return {"success": True, "people": people, "count": len(people)}

    # ── Document + image combo ─────────────────────────────────────────────────

    @staticmethod
    def analyze_document_with_vision(path: str, question: str = "") -> Dict:
        """
        Smart document analysis: extracts text for text-based docs,
        uses Claude vision for scanned/image-heavy PDFs.
        Returns {"text", "vision_analysis", "combined"}.
        """
        ext = Path(path).suffix.lower()

        text_result = MediaTools.read_document(path)
        text        = text_result.get("text", "") if text_result.get("success") else ""

        vision_analysis = ""
        if ext == ".pdf" and _PIL_OK and _PDF_OK:
            # Also render first page as image for visual analysis
            try:
                import pdfplumber
                with pdfplumber.open(path) as pdf:
                    if pdf.pages:
                        pg_img = pdf.pages[0].to_image(resolution=150)
                        _ensure_dir(_TEMP_DIR)
                        tmp_img = str(_TEMP_DIR / "page1.png")
                        pg_img.save(tmp_img)
                        q = question or "Analyze this document page — extract any visual elements, diagrams, charts, signatures, or important layout information not captured in plain text."
                        vision_analysis = _claude_vision([tmp_img], q)
                        try:
                            os.remove(tmp_img)
                        except Exception:
                            pass
            except Exception:
                pass

        elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            r = MediaTools.analyze_image(path, question)
            vision_analysis = r.get("analysis", "")

        combined = ""
        if text and vision_analysis:
            combined = f"=== EXTRACTED TEXT ===\n{text}\n\n=== VISUAL ANALYSIS ===\n{vision_analysis}"
        elif text:
            combined = text
        elif vision_analysis:
            combined = vision_analysis

        return {
            "success":         True,
            "path":            path,
            "text":            text[:10000] if text else "",
            "vision_analysis": vision_analysis,
            "combined":        combined[:15000],
            "pages":           text_result.get("pages", 0),
            "tables":          text_result.get("tables", []),
        }
