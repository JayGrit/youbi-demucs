# demucs Plan

## Responsibility

`demucs` separates source media into vocals and background audio. It owns Demucs
model loading and GPU/CPU device selection.

## Input Table

`demucs`

Required fields:

- `task_id`
- `video_source_path`
- `status = 'ready'`

## Outputs

- `audio_vocals_path`
- `audio_bgm_path`

It copies `audio_vocals_path` into `whisper.audio_vocals_path`.

## Polling

Poll one ready row every `POLL_INTERVAL_SECONDS`.

## Processing

1. Mark row `running`.
2. Validate source video exists.
3. Run Demucs.
4. Validate both output WAV files exist and are non-empty.
5. Mark demucs `success`.
6. Mark whisper `ready`.

## Failure Handling

Mark demucs and task as `failed`. Do not delete model cache or partial outputs.

## Later Work

- Add GPU capacity controls.
- Add device-specific service deployment.
- Add output checksum fields.

