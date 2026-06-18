from __future__ import annotations

import argparse
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ydbi_demucs.demucs import separate_audio

log = logging.getLogger(__name__)
DESKTOP_DIR = Path.home() / "Desktop"


@dataclass(frozen=True)
class LocalDemucsResult:
    vocals: Path
    bgm: Path


def separate_local_file(input_file: str | Path) -> LocalDemucsResult:
    """Separate a local audio/video file and keep vocals/background WAV outputs on Desktop."""
    source = Path(input_file).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"input file does not exist: {source}")

    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ydbi-demucs-local-") as temp_dir:
        session = Path(temp_dir)
        vocals, bgm = separate_audio(source, session)
        vocals_output = DESKTOP_DIR / f"{source.stem}_vocal.wav"
        bgm_output = DESKTOP_DIR / f"{source.stem}_back.wav"
        shutil.copy2(vocals, vocals_output)
        shutil.copy2(bgm, bgm_output)

    return LocalDemucsResult(vocals=vocals_output, bgm=bgm_output)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Separate a local audio/video file into Desktop vocals/background WAV files.",
    )
    parser.add_argument("input_file", help="local input media file, audio or video")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="logging level",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    result = separate_local_file(args.input_file)
    print(f"vocals: {result.vocals}")
    print(f"bgm: {result.bgm}")


if __name__ == "__main__":
    main()
