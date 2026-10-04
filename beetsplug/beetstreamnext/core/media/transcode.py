from __future__ import annotations

import os
import subprocess
import math
import tempfile
from pathlib import Path
import queue
import threading
from typing import Generator, Optional, Any
import flask

from beetsplug.beetstreamnext.constants import FFMPEG_PYTHON, TRANSCODE_TMP_DIR
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.utils.general import send_file
from beetsplug.beetstreamnext.utils.system import find_binary



FORMAT_MAP = {
    # Lossy
    'mp3':  {'f': 'mp3',  'c': 'libmp3lame', 'mime': 'audio/mpeg',      'lossless': False},
    'ogg':  {'f': 'ogg',  'c': 'libvorbis',  'mime': 'audio/ogg',       'lossless': False},
    'opus': {'f': 'opus', 'c': 'libopus',    'mime': 'audio/ogg',       'lossless': False},
    'aac':  {'f': 'adts', 'c': 'aac',         'mime': 'audio/aac',      'lossless': False},
    'm4a':  {'f': 'mp4',  'c': 'aac',         'mime': 'audio/mp4',      'lossless': False,
             'flags': 'frag_keyframe+empty_moov+default_base_moof'},
    'wma':  {'f': 'asf',  'c': 'wmav2',       'mime': 'audio/x-ms-wma', 'lossless': False},

    # Lossless
    'flac': {'f': 'flac', 'c': 'flac',        'mime': 'audio/flac',     'lossless': True},
    'alac': {'f': 'ipod', 'c': 'alac',        'mime': 'audio/mp4',      'lossless': True,
             'flags': 'frag_keyframe+empty_moov+default_base_moof'},
    'wav':  {'f': 'wav',  'c': 'pcm_s16le',   'mime': 'audio/wav',      'lossless': True},
    'aiff': {'f': 'aiff', 'c': 'pcm_s16be',   'mime': 'audio/aiff',     'lossless': True},
}


def is_lossless(fmt: str) -> bool:
    """Identify if a format key or file extension is lossless."""
    fmt = fmt.lower()
    if fmt in FORMAT_MAP:
        return FORMAT_MAP[fmt]['lossless']
    # Extensions that might be source files but not necessarily transcode targets
    return fmt in {'flac', 'alac', 'wav', 'aiff', 'ape', 'wma lossless', 'dsf', 'dff'}


def evaluate_limitation(actual_val: Any, limit_obj: dict) -> bool:
    """Evaluates a ClientInfo limitation object against an actual value."""

    comp = limit_obj.get('comparison')
    values = limit_obj.get('values', [])
    if not values:
        return True

    try:
        if comp == 'LessThanEqual':
            return float(actual_val) <= float(values[0])
        if comp == 'GreaterThanEqual':
            return float(actual_val) >= float(values[0])
        if comp == 'Equals':
            return str(actual_val) in [str(v) for v in values]
        if comp == 'NotEquals':
            return str(actual_val) not in [str(v) for v in values]
    except (ValueError, TypeError):
        return False
    return True


def get_normalization_filter(item) -> str | None:
    """
    Calculates the ReplayGain adjustment and peak limiting.
    Returns an FFmpeg audio filter string.
    """
    if not app.config.get('replaygain_enabled', True):
        return None

    # Beets stores these as floats
    # rg_track_gain is in dB
    # rg_track_peak is a ratio
    gain = item.get('rg_track_gain')
    peak = item.get('rg_track_peak')

    # Fallback for files without ReplayGain tags
    if gain is None:
        gain = app.config.get('replaygain_fallback', -6.0)

    # Apply user preamp
    gain += app.config.get('replaygain_preamp', 0.0)

    # Safety peak limiting
    if app.config.get('audio_peak_limit', True):
        # Must ensure that: 10^(gain/20) * peak <= 1.0
        # If peak is missing, assume 1.0 (safe default)
        track_peak = peak if peak is not None else 1.0

        if track_peak > 0:
            requested_gain_factor = 10 ** (gain / 20.0)
            max_allowed_gain_factor = 1.0 / track_peak

            if requested_gain_factor > max_allowed_gain_factor:
                # Reduce gain to the absolute ceiling to prevent clipping
                gain = 20 * math.log10(max_allowed_gain_factor)
                bsn_logger.debug(f"Peak limit triggered for {item.get('title')}: clamped gain to {gain:.2f}dB")

    # Final filter: volume adjustment + a hard limiter at -0.1dB as a safety net
    return f'volume={gain:.2f}dB,alimiter=limit=0.99'


def _send_transcode(
        file_path: str | Path,
        start_at: float = 0.0,
        max_bitrate: int = 128,
        req_format: str = 'mp3',
        duration: float = 0.0,
        estimate_length: bool = False,
        audio_filters: Optional[str] = None
    ) -> flask.Response | None:

    target = FORMAT_MAP.get(req_format.lower() if req_format else 'mp3', FORMAT_MAP['mp3'])
    target_lossless = target['lossless']
    ffmpeg_bin = find_binary('ffmpeg')

    if FFMPEG_PYTHON:
        import ffmpeg
        input_stream = ffmpeg.input(str(file_path), ss=start_at) if start_at > 0 else ffmpeg.input(str(file_path))

        output_args = {
            'format': target['f'],
            'acodec': target['c'],
            'map_metadata': '-1'
        }

        if 'flags' in target:
            output_args['movflags'] = target['flags']

        if not target.get('lossless'):
            output_args['audio_bitrate'] = f'{max_bitrate}k'

        if audio_filters:
            output_args['af'] = audio_filters

        output_stream = (
            input_stream
            .audio
            .output('pipe:', **output_args)
            .run_async(pipe_stdout=True, quiet=True, cmd=ffmpeg_bin or 'ffmpeg')
        )

    elif ffmpeg_bin:
        command = [ffmpeg_bin, '-hide_banner', '-loglevel', 'error']

        if start_at > 0:
            command.extend(["-ss", f"{start_at:.2f}"])

        command.extend(['-i', str(file_path)])

        # Apply optional audio filters
        if audio_filters:
            command.extend(['-af', audio_filters])

        command.extend([
            '-vn',          # strip cover art, otherwise many clients just crash
            '-map_metadata', '-1',
            '-f', str(target['f']),
            '-c:a', str(target['c']),
        ])

        if 'flags' in target:
            command.extend(['-movflags', str(target['flags'])])

        # Only apply bitrate to lossy formats
        if not target_lossless:
            command.extend(['-b:a', f'{max_bitrate}k'])

        command.append('pipe:1')

        output_stream = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    else:
        return None

    def generate() -> Generator:
        chunk_queue: queue.Queue = queue.Queue(maxsize=32)
        _SENTINEL = object()   # marks "reader finished"
        stop_event = threading.Event()

        def _reader() -> None:
            try:
                while not stop_event.is_set():
                    try:
                        chunk = output_stream.stdout.read(8192)
                    except (OSError, ValueError):
                        break
                    if not chunk:
                        break
                    while not stop_event.is_set():
                        try:
                            chunk_queue.put(chunk, timeout=0.5)
                            # timeout put to detect stop_event even when consumer has stopped draining the queue
                            break
                        except queue.Full:
                            continue
            finally:
                # if queue is full, consumer is gone and won't read the sentinel anyway
                try:
                    chunk_queue.put_nowait(_SENTINEL)
                except queue.Full:
                    pass

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        try:
            while True:
                try:
                    chunk = chunk_queue.get(timeout=2.0)
                except queue.Empty:
                    # No data for 2s, is ffmpeg still alive?
                    if output_stream.poll() is not None:
                        break
                    continue

                if chunk is _SENTINEL:
                    break
                yield chunk
        finally:
            stop_event.set()
            try:
                output_stream.terminate()
                try:
                    output_stream.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    output_stream.kill()
                    output_stream.wait(timeout=5)
            except Exception:
                pass

    # reader is a daemon, here stdout is closed and stop_event is set, so it will exit on its own. Joining would block.

    response = flask.Response(flask.stream_with_context(generate()), mimetype=target['mime'])

    if estimate_length and max_bitrate > 0 and duration > 0:
        remaining = max(0.0, duration - start_at)
        estimated_bytes = int((max_bitrate * 1000 / 8) * remaining)
        response.headers['Content-Length'] = estimated_bytes


    response.headers['Accept-Ranges'] = 'none'

    return response


def _send_transcode_tempfile(
        file_path: str | Path,
        start_at: float = 0.0,
        max_bitrate: int = 128,
        req_format: str = 'mp3',
        audio_filters: Optional[str] = None
    ) -> flask.Response | None:
    """
    Transcode to a temp file then serve with send_file() for an exact Content-Length and Range/seek support.
    """

    target = FORMAT_MAP.get(req_format.lower() if req_format else 'mp3', FORMAT_MAP['mp3'])
    ffmpeg_bin = find_binary('ffmpeg') or 'ffmpeg'

    fd, tmp_name = tempfile.mkstemp(suffix=f".{target['f']}", dir=TRANSCODE_TMP_DIR)
    os.close(fd)
    tmp_path = Path(tmp_name)

    command = [ffmpeg_bin, '-hide_banner', '-loglevel', 'error', '-y']

    if start_at > 0:
        command.extend(['-ss', f'{start_at:.2f}'])

    command.extend(['-i', str(file_path)])

    if audio_filters:
        command.extend(['-af', audio_filters])

    command.extend([
        '-vn',
        '-map_metadata', '-1',
        '-f', str(target['f']),
        '-c:a', str(target['c']),
    ])

    if 'flags' in target:
        command.extend(['-movflags', str(target['flags'])])

    if not target['lossless']:
        command.extend(['-b:a', f'{max_bitrate}k'])

    command.append(str(tmp_path))

    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=600)
    except (subprocess.TimeoutExpired, OSError) as e:
        bsn_logger.warning(f"Full transcode of '{file_path}' failed: {e}")
        tmp_path.unlink(missing_ok=True)
        return None

    if result.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size == 0:
        stderr = result.stderr.decode('utf-8', errors='replace')[:500]
        bsn_logger.warning(f"Full transcode of '{file_path}' failed (rc={result.returncode}): {stderr}")
        tmp_path.unlink(missing_ok=True)
        return None

    # Bypass send_file()'s reverse-proxy offload which (would race the temp files's deletion below)
    response = flask.send_file(tmp_path, mimetype=target['mime'], conditional=True)

    @response.call_on_close
    def _cleanup_tempfile() -> None:
        tmp_path.unlink(missing_ok=True)

    return response


def try_transcode(
        file_path: str | Path,
        start_at: float = 0.0,
        max_bitrate: int = 128,
        req_format: str = 'mp3',
        duration: float = 0.0,
        estimate_length: bool = False,
        audio_filters: Optional[str] = None,
        exact_length: bool = False
    ) -> flask.Response | None:

    if not (FFMPEG_PYTHON or find_binary('ffmpeg')):
        return send_file(file_path)

    if exact_length:
        return _send_transcode_tempfile(
            file_path=file_path,
            start_at=start_at,
            max_bitrate=max_bitrate,
            req_format=req_format,
            audio_filters=audio_filters
        )

    return _send_transcode(
        file_path=file_path,
        start_at=start_at,
        max_bitrate=max_bitrate,
        req_format=req_format,
        duration=duration,
        estimate_length=estimate_length,
        audio_filters=audio_filters
    )
