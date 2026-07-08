from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Train YOLO on the smart-home real-photo dataset.")
    parser.add_argument("--data", default="data/yolo_real/dataset.yaml", help="YOLO dataset YAML path.")
    parser.add_argument("--model", default="yolov8n.pt", help="Base model, for example yolov8n.pt or yolov8s.pt.")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default=None, help="Use 0 for first GPU, cpu for CPU, or leave empty.")
    parser.add_argument("--project", default="runs/detect")
    parser.add_argument("--name", default="smart_home_yolo")
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--lr0", type=float, default=None, help="Initial learning rate passed to Ultralytics.")
    parser.add_argument("--lrf", type=float, default=None, help="Final learning-rate fraction passed to Ultralytics.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--cleanup-data",
        action="store_true",
        help="Delete data/yolo_real and data/open_images_raw after successful training.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    data = Path(args.data)
    if not data.is_absolute():
        data = root / data
    if not data.exists():
        raise FileNotFoundError(f"Dataset YAML not found: {data}")

    from ultralytics import YOLO

    model = YOLO(args.model)
    train_kwargs = {
        "data": str(data),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "project": str((root / args.project).resolve()),
        "name": args.name,
        "patience": args.patience,
        "resume": args.resume,
    }
    if args.device:
        train_kwargs["device"] = args.device
    if args.lr0 is not None:
        train_kwargs["lr0"] = args.lr0
    if args.lrf is not None:
        train_kwargs["lrf"] = args.lrf

    results = model.train(**train_kwargs)
    save_dir = Path(getattr(results, "save_dir", root / args.project / args.name))
    best = save_dir / "weights" / "best.pt"
    print(f"Training finished: {save_dir}")
    print(f"Best weights: {best}")
    print("Copy best.pt to models/best.pt or set YOLO_MODEL_PATH to use it in the backend.")

    if args.cleanup_data:
        cleanup_training_data(root)


def cleanup_training_data(root: Path) -> None:
    targets = [
        root / "data" / "yolo_real",
        root / "data" / "yolo_required",
        root / "data" / "yolo_required_filtered",
        root / "data" / "open_images_raw" / "images",
        root / "data" / "required_objects_raw",
        root / "data" / "required_objects_raw_filtered",
    ]
    for raw_target in targets:
        target = raw_target.resolve()
        if target.exists() and root.resolve() in target.parents:
            shutil.rmtree(target)
            print(f"Deleted training images/cache: {target}")
    print("Kept Open Images CSV metadata so the next dataset build is faster.")


if __name__ == "__main__":
    main()
