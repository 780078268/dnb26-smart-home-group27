from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from .yolo_contract import (
        OPEN_VOCAB_PROMPTS,
        dedupe_labels,
        default_custom_model_path,
        default_open_vocab_model_path,
        ensure_mobileclip_weight,
        ensure_ultralytics_runtime_env,
        normalize_detection,
    )
except ImportError:
    from yolo_contract import (
        OPEN_VOCAB_PROMPTS,
        dedupe_labels,
        default_custom_model_path,
        default_open_vocab_model_path,
        ensure_mobileclip_weight,
        ensure_ultralytics_runtime_env,
        normalize_detection,
    )


class Group27YoloService:
    """YOLO adapter that always returns the group interface shape.

    Output contract:
        [{"label": "drone", "confidence": 0.91}, ...]
    """

    def __init__(
        self,
        *,
        mode: str = "open_vocab",
        model_path: str | Path | None = None,
        fallback_model_path: str | Path | None = None,
        prompts: list[str] | None = None,
        device: str | None = None,
    ) -> None:
        self.mode = mode
        self.model_path = str(model_path or default_open_vocab_model_path())
        self.fallback_model_path = Path(fallback_model_path or default_custom_model_path())
        self.prompts = prompts or list(OPEN_VOCAB_PROMPTS)
        self.device = device
        self._model: Any | None = None
        self._active_mode: str | None = None

    @property
    def active_mode(self) -> str | None:
        return self._active_mode

    def detect(
        self,
        source: str | Path,
        *,
        conf: float = 0.35,
        imgsz: int = 768,
        augment: bool = False,
        save: bool = False,
    ) -> list[dict[str, Any]]:
        model = self._load_model()
        kwargs: dict[str, Any] = {
            "conf": conf,
            "imgsz": imgsz,
            "augment": augment,
            "save": save,
            "verbose": False,
        }
        if self.device:
            kwargs["device"] = self.device
        results = model.predict(str(source), **kwargs)
        return self._extract_labels(results)

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        if self.mode in {"open_vocab", "auto"}:
            try:
                self._model = self._load_open_vocab_model()
                self._active_mode = "open_vocab"
                return self._model
            except Exception:
                if self.mode != "auto":
                    raise

        self._model = self._load_custom_model()
        self._active_mode = "custom"
        return self._model

    def _load_open_vocab_model(self) -> Any:
        ensure_ultralytics_runtime_env()
        ensure_mobileclip_weight()
        try:
            from ultralytics import YOLOE as ModelClass
        except ImportError as exc:
            try:
                from ultralytics import YOLO as ModelClass
            except ImportError:
                raise RuntimeError(
                    "ultralytics with YOLOE support is required. Install dependencies from requirements.txt."
                ) from exc

        model = ModelClass(self.model_path)
        if not hasattr(model, "set_classes"):
            raise RuntimeError("The installed ultralytics package does not support open-vocabulary set_classes().")
        model.set_classes(self.prompts)
        return model

    def _load_custom_model(self) -> Any:
        ensure_ultralytics_runtime_env()
        from ultralytics import YOLO

        if not self.fallback_model_path.exists():
            raise FileNotFoundError(f"Custom YOLO model not found: {self.fallback_model_path}")
        return YOLO(str(self.fallback_model_path))

    def _extract_labels(self, results: Any) -> list[dict[str, Any]]:
        labels: list[dict[str, Any]] = []
        for result in results:
            names = result.names or {}
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                cls_id = int(box.cls[0])
                raw_label = names.get(cls_id, cls_id)
                normalized = normalize_detection(raw_label, float(box.conf[0]))
                if normalized is not None:
                    labels.append(normalized)
        return dedupe_labels(labels)
