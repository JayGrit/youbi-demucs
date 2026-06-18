from __future__ import annotations

import argparse
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ydbi_demucs.demucs import separate_audio

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalDemucsResult:
    vocals: Path
    bgm: Path


def separate_local_file(input_file: str | Path, output_dir: str | Path | None = None) -> LocalDemucsResult:
    """Separate a local audio/video file and keep vocals/background WAV outputs."""
    source = Path(input_file).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"input file does not exist: {source}")

    destination = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else source.with_name(f"{source.stem}_demucs")
    )
    destination.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ydbi-demucs-local-") as temp_dir:
        session = Path(temp_dir)
        vocals, bgm = separate_audio(source, session)
        vocals_output = destination / "audio_vocals.wav"
        bgm_output = destination / "audio_bgm.wav"
        shutil.copy2(vocals, vocals_output)
        shutil.copy2(bgm, bgm_output)

    return LocalDemucsResult(vocals=vocals_output, bgm=bgm_output)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Separate a local audio/video file into vocals and background audio.",
    )
    parser.add_argument("input_file", help="local input media file, audio or video")
    parser.add_argument(
        "-o",
        "--output-dir",
        help="output directory, defaults to <input_stem>_demucs next to the input file",
    )
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
    result = separate_local_file(args.input_file, args.output_dir)
    print(f"vocals: {result.vocals}")
    print(f"bgm: {result.bgm}")


if __name__ == "__main__":
    main()
