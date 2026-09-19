from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from django.conf import settings

logger = logging.getLogger("core.pipeline")


@dataclass
class PreprocessedImage:
    original_path: str
    preprocessed_path: str
    width: int
    height: int
    rotation_degrees: float = 0.0
    template_match_score: float = 0.0
    template_name: str = ""
    regions: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class ImagePreprocessor:
    def __init__(self):
        self._cv = None
        self._pillow = None

    def _load_cv(self):
        if self._cv is None:
            try:
                import cv2
                self._cv = cv2
            except Exception as e:
                logger.warning("OpenCV no disponible: %s", e)
        return self._cv

    def _load_pil(self):
        if self._pillow is None:
            from PIL import Image
            self._pillow = Image
        return self._pillow

    def preprocess(self, src_path: str) -> PreprocessedImage:
        result = PreprocessedImage(original_path=src_path, preprocessed_path=src_path, width=0, height=0)
        pil = self._load_pil()
        try:
            img = pil.open(src_path)
            result.width, result.height = img.size
        except Exception as e:
            result.warnings.append(f"No se pudo abrir la imagen: {e}")
            return result

        cv2 = self._load_cv()
        if cv2 is None:
            return result

        try:
            import numpy as np
            arr = np.array(img.convert("RGB"))
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
            denoised = cv2.fastNlMeansDenoising(enhanced, h=10)

            try:
                coords = np.column_stack(np.where(denoised > 0))
                angle = cv2.minAreaRect(coords)[-1]
                if angle < -45:
                    angle = -(90 + angle)
                else:
                    angle = -angle
                if abs(angle) > 0.2:
                    (h, w) = denoised.shape[:2]
                    center = (w // 2, h // 2)
                    M = cv2.getRotationMatrix2D(center, angle, 1.0)
                    denoised = cv2.warpAffine(
                        denoised, M, (w, h),
                        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
                    )
                    result.rotation_degrees = float(angle)
            except Exception as e:
                result.warnings.append(f"Rotación automática omitida: {e}")

            result.regions = self._detect_regions(denoised)
            result.template_match_score = 0.70 if result.regions else 0.25
            result.template_name = "regional" if "gobernador" in result.regions else "provincial_distrital"

            out_dir = os.path.join(settings.MEDIA_ROOT, "preprocessed")
            os.makedirs(out_dir, exist_ok=True)
            base = os.path.splitext(os.path.basename(src_path))[0]
            out_path = os.path.join(out_dir, f"{base}_prep.png")
            cv2.imwrite(out_path, denoised)
            result.preprocessed_path = out_path
        except Exception as e:
            logger.exception("Error en preprocesamiento")
            result.warnings.append(f"Preprocesamiento parcial: {e}")
        return result

    def _detect_regions(self, gray_arr) -> dict:
        cv2 = self._cv
        if cv2 is None:
            return {}
        try:
            _, binary = cv2.threshold(gray_arr, 128, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
            contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            h, w = gray_arr.shape[:2]
            return {
                "header": {"x": 0, "y": 0, "w": w, "h": int(h * 0.15)},
                "table_info": {"x": int(w * 0.05), "y": int(h * 0.15), "w": int(w * 0.9), "h": int(h * 0.10)},
                "main_table": {"x": int(w * 0.05), "y": int(h * 0.28), "w": int(w * 0.85), "h": int(h * 0.55)},
                "totals": {"x": int(w * 0.05), "y": int(h * 0.83), "w": int(w * 0.85), "h": int(h * 0.14)},
                "contours_count": len(contours),
            }
        except Exception as e:
            logger.warning("Detección de regiones fallida: %s", e)
            return {}


class OCREngine:
    def __init__(self):
        self._pytesseract = None
        self._configured = False
        self._config_error: Optional[str] = None

    def _setup(self):
        if self._configured:
            return
        self._configured = True
        try:
            import pytesseract
            self._pytesseract = pytesseract
            if getattr(settings, "TESSERACT_CMD", ""):
                pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD
        except Exception as e:
            self._config_error = str(e)
            logger.warning("Tesseract no disponible: %s", e)

    def extract_regions(self, preprocessed: PreprocessedImage) -> dict:
        self._setup()
        result = {"global": "", "regions": {}, "numbers": []}
        if self._pytesseract is None:
            return result
        try:
            from PIL import Image
            import numpy as np
            import cv2
            img = cv2.imread(preprocessed.preprocessed_path)
            if img is None:
                return result
            for name, coords in preprocessed.regions.items():
                if not isinstance(coords, dict) or "x" not in coords:
                    continue
                x, y, w, h = coords["x"], coords["y"], coords["w"], coords["h"]
                if x + w > img.shape[1]:
                    w = img.shape[1] - x
                if y + h > img.shape[0]:
                    h = img.shape[0] - y
                if w <= 0 or h <= 0:
                    continue
                crop = img[y:y + h, x:x + w]
                try:
                    text = self._pytesseract.image_to_string(crop, lang="spa+eng", config="--psm 6")
                    result["regions"][name] = text.strip()
                except Exception as e:
                    result["regions"][name] = f"<error: {e}>"
            try:
                result["global"] = self._pytesseract.image_to_string(img, lang="spa+eng")
            except Exception as e:
                result["global"] = ""
                logger.warning("OCR global falló: %s", e)
            result["numbers"] = [int(x) for x in re.findall(r"\d+", result["global"])]
        except Exception:
            logger.exception("OCR general falló")
        return result

    def extract_number_field(self, image_arr) -> str:
        self._setup()
        if self._pytesseract is None:
            return ""
        try:
            txt = self._pytesseract.image_to_string(
                image_arr, lang="spa+eng",
                config="--psm 7 -c tessedit_char_whitelist=0123456789",
            )
            return re.sub(r"\D", "", txt)
        except Exception:
            return ""
