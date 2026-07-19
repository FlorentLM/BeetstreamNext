import os
import subprocess
import math
from pathlib import Path
import queue
import threading
from typing import Generator, Optional, Any
import flask

from .. import api_bp

from beetsplug.beetstreamnext.constants import FFMPEG_PYTHON, FFMPEG_BIN
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.utils.general import api_bool
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.utils.system import get_mimetype
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.api.serializers import IDMapper


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


def _send_direct(file_path: str | Path) -> flask.Response | None:
    try:
        return flask.send_file(file_path, mimetype=get_mimetype(file_path))
    except OSError as e:
        bsn_logger.error(f"Failed to serve file '{file_path}': {e}")
        return None


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

    if FFMPEG_PYTHON:
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
            .run_async(pipe_stdout=True, quiet=True)
        )

    elif FFMPEG_BIN:
        command = ['ffmpeg', '-hide_banner', '-loglevel', 'error']

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

    # TODO: Not sure if it's important to tell clients they cant seek with HTTP range headers?
    # response.headers['Accept-Ranges'] = 'none'
    # response.headers['Cache-Control'] = 'no-cache'

    return response


def try_transcode(
        file_path: str | Path,
        start_at: float = 0.0,
        max_bitrate: int = 128,
        req_format: str = 'mp3',
        duration: float = 0.0,
        estimate_length: bool = False,
        audio_filters: Optional[str] = None
    ) -> flask.Response | None:

    if FFMPEG_PYTHON or FFMPEG_BIN:
        return _send_transcode(
            file_path=file_path,
            start_at=start_at,
            max_bitrate=max_bitrate,
            req_format=req_format,
            duration=duration,
            estimate_length=estimate_length,
            audio_filters=audio_filters
        )

    else:
        return _send_direct(file_path)


##
# Endpoints

# Spec: https://opensubsonic.netlify.app/docs/endpoints/stream/
@api_bp.route('/stream', methods=["GET", "POST"])
@api_bp.route('/stream.view', methods=["GET", "POST"])
def endpoint_stream_song() -> flask.Response | None:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    song_id = r.get('id', default='', type=safe_str)             # Required
    max_bitrate = r.get('maxBitRate', default=0, type=int)
    req_format = r.get('format', default='raw', type=safe_str)
    time_offset = r.get('timeOffset', default=0.0, type=float)
    estimate_length = r.get('estimateContentLength', default=False, type=api_bool)

    if not bool(flask.g.user_data.get('streamRole')):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not song_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    user_max_bitrate = flask.g.user_data.get('maxBitRate', 0)
    if user_max_bitrate > 0:
        max_bitrate = min(user_max_bitrate, max_bitrate) if max_bitrate > 0 else user_max_bitrate

    beets_song_id = IDMapper.sub_to_song(song_id)
    song = flask.g.lib.get_item(beets_song_id)
    song_path = os.fsdecode(song.get('path', b'')) if song else ''

    if song_path:
        path_obj = Path(song_path)
        if not path_obj.is_absolute():
            song_path = str(app.config['root_directory'] / path_obj)

        song_ext = song_path.rsplit('.', 1)[-1].lower() if '.' in song_path else ''
        norm_filter = get_normalization_filter(song)
        needs_transcode = False

        # Transcode if audio normalisation is required
        if norm_filter:
            needs_transcode = True

        # Transcode if bitrate too high
        elif max_bitrate > 0 and song.get('bitrate', 0) > (max_bitrate * 1000):
            needs_transcode = True

        # or if client wants different format
        elif req_format != 'raw' and req_format != song_ext and not app.config['never_transcode']:
            needs_transcode = True

        # or if seeking
        elif time_offset > 0:
            needs_transcode = True

        if not needs_transcode:
            response = _send_direct(song_path)
        else:
            target_bitrate = max_bitrate if max_bitrate > 0 else 320

            return try_transcode(       # TODO: Should this return a subsonic error or 404?
                song_path,
                start_at=time_offset,
                max_bitrate=target_bitrate,
                req_format=req_format if req_format != 'raw' else 'mp3',
                duration=song.get('length') or 0.0,
                estimate_length=estimate_length,
                audio_filters=norm_filter
            )

        if response is not None:
            return response

        bsn_logger.warning(f"Direct play of song '{Path(song_path).name}' failed.")

    return subsonic_error(70, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/download/
@api_bp.route('/download', methods=["GET", "POST"])
@api_bp.route('/download.view', methods=["GET", "POST"])
def endpoint_download_song() -> flask.Response | None:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    song_id = r.get('id', default='', type=safe_str)         # Required

    if not bool(flask.g.user_data.get('downloadRole')):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not song_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    beets_song_id = IDMapper.sub_to_song(song_id)
    item = flask.g.lib.get_item(beets_song_id)

    song_path = os.fsdecode(item.get('path', b'')) if item else ''
    if song_path:
        path_obj = Path(song_path)
        if not path_obj.is_absolute():
            song_path = str(app.config['root_directory'] / path_obj)

    if not song_path:
        return subsonic_error(70, resp_fmt=resp_fmt)

    return _send_direct(song_path)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/gettranscodedecision/
@api_bp.route('/getTranscodeDecision', methods=["POST"])
@api_bp.route('/getTranscodeDecision.view', methods=["POST"])
def endpoint_get_transcode_decision() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    media_id = r.get('mediaId', default='', type=safe_str)              # Required
    media_type = r.get('mediaType', default='song', type=safe_str)      # Required

    # TODO: media_type can be podcast once podcassts are supported by BSN

    client_info = flask.request.get_json(silent=True) or {}
    if not media_id or not media_type:
        return subsonic_error(10, resp_fmt=resp_fmt)

    beets_id = IDMapper.sub_to_song(media_id)
    item = flask.g.lib.get_item(beets_id)
    if not item:
        return subsonic_error(70, resp_fmt=resp_fmt)

    # Source info
    source_format = (item.format or '').lower()
    source_bitrate = int(item.bitrate or 0)
    source_is_lossless = is_lossless(source_format)

    # User profile limit (in bps)
    user_max_br = flask.g.user_data.get('maxBitRate', 0) * 1000

    source_stream = {
        'protocol': 'http',
        'container': source_format,
        'codec': source_format,
        'audioChannels': int(item.channels or 2),
        'audioBitrate': source_bitrate,
        'audioSamplerate': int(item.samplerate or 44100),
        'audioBitdepth': int(item.bitdepth or 16)
    }

    reasons = []
    can_direct_play = True

    # Server constraints
    norm_filter = get_normalization_filter(item)
    if norm_filter:
        can_direct_play = False
        reasons.append('ServerSideProcessingRequired')

    if user_max_br > 0 and source_bitrate > user_max_br:
        can_direct_play = False
        reasons.append('BitrateTooHigh')

    # Client support (direct play)
    if can_direct_play:
        direct_profiles = client_info.get('directPlayProfiles', [])
        supported_profile = next((p for p in direct_profiles if source_format in p.get('containers', [])), None)

        if not supported_profile:
            can_direct_play = False
            reasons.append('ContainerNotSupported')
        else:
            codec_profiles = client_info.get('codecProfiles', [])
            relevant_codec = next((c for c in codec_profiles if c.get('name') == source_format), None)
            if relevant_codec:
                for limit in relevant_codec.get('limitations', []):
                    attr = limit.get('name')
                    val_map = {
                        'audioBitrate': source_bitrate,
                        'audioChannels': source_stream['audioChannels'],
                        'audioSamplerate': source_stream['audioSamplerate'],
                        'audioBitdepth': source_stream['audioBitdepth']
                    }
                    if attr in val_map and not evaluate_limitation(val_map[attr], limit):
                        can_direct_play = False
                        reasons.append(f'{attr}LimitExceeded')

    # Transcoding selection
    can_transcode = (FFMPEG_BIN or FFMPEG_PYTHON)
    transcode_stream = None

    if not can_direct_play and can_transcode:
        tx_profiles = client_info.get('transcodingProfiles', [])
        selected_profile = None

        for profile in tx_profiles:
            target_container = profile.get('container', '').lower()

            # Check if server supports this target container
            if target_container not in FORMAT_MAP:
                continue

            target_lossless = FORMAT_MAP[target_container]['lossless']

            # If user/server bitrate limit is set, do not use lossless transcoding
            if user_max_br > 0 and target_lossless:
                continue

            # Never transcode lossy source to lossless target (wasteful)
            if not source_is_lossless and target_lossless:
                continue

            # This is the best profile based on client preference order + server constraints
            selected_profile = profile
            break

        target_container = selected_profile['container'].lower() if selected_profile else 'mp3'
        target_lossless = FORMAT_MAP[target_container]['lossless']

        # Target bitrate: start with client's suggested max
        target_br = client_info.get('maxTranscodingAudioBitrate', 320000)

        # Apply user limit if needed
        if user_max_br > 0:
            target_br = min(target_br, user_max_br)

        # If transcoding lossy -> lossy, do not up-sample bitrate
        if not source_is_lossless and not target_lossless:
            target_br = min(target_br, source_bitrate)

        transcode_stream = {
            'protocol': selected_profile.get('protocol', 'http') if selected_profile else 'http',
            'container': target_container,
            'codec': selected_profile.get('audioCodec', target_container) if selected_profile else target_container,
            'audioChannels': min(source_stream['audioChannels'], 2),
            # Subsonic spec: bitrate is 0 or null for lossless
            'audioBitrate': target_br if not target_lossless else 0,
            'audioSamplerate': min(source_stream['audioSamplerate'], 48000),
            'audioBitdepth': 16 if not target_lossless else source_stream['audioBitdepth']
        }

        # Encode transcode instructions into a opaque string for getTranscodeStream
        tx_params = f'{target_container}|{target_br}|{int(bool(norm_filter))}'

    decision = {
        'canDirectPlay': can_direct_play,
        'canTranscode': bool(transcode_stream),  # true only if valid path found
        'transcodeReason': reasons,
    }

    if source_stream:
        decision['sourceStream'] = source_stream

    if transcode_stream:
        decision['transcodeStream'] = transcode_stream
        decision['transcodeParams'] = tx_params

    payload = {
        'transcodeDecision': decision
    }

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/gettranscodestream/
@api_bp.route('/getTranscodeStream', methods=["GET", "POST"])
@api_bp.route('/getTranscodeStream.view', methods=["GET", "POST"])
def endpoint_get_transcode_stream() -> flask.Response | None:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    media_id = r.get('mediaId', default='', type=safe_str)              # Required
    media_type = r.get('mediaType', default='song', type=safe_str)      # Required
    offset = r.get('offset', default=0.0, type=float)
    tx_params_raw = r.get('transcodeParams', default='', type=str)      # Required

    # TODO: media_type can be podcast once podcassts are supported by BSN

    if not bool(flask.g.user_data.get('streamRole')):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not media_id or not tx_params_raw:
        return subsonic_error(10, resp_fmt=resp_fmt)

    try:
        # container | bitrate | norm
        parts = tx_params_raw.split('|')
        req_format = parts[0]
        max_bitrate = int(float(parts[1]) / 1000) # bps to kbps
        apply_norm = parts[2] == '1'
    except (IndexError, ValueError):
        return subsonic_error(0, 'Invalid transcodeParams', resp_fmt=resp_fmt)

    beets_song_id = IDMapper.sub_to_song(media_id)
    song = flask.g.lib.get_item(beets_song_id)
    if not song:
        return subsonic_error(70, resp_fmt=resp_fmt)

    song_path = os.fsdecode(song.get('path', b''))
    if not song_path:
        return subsonic_error(70, resp_fmt=resp_fmt)

    path_obj = Path(song_path)
    if not path_obj.is_absolute():
        song_path = str(app.config['root_directory'] / path_obj)

    norm_filter = get_normalization_filter(song) if apply_norm else None

    return try_transcode(
        song_path,
        start_at=offset,
        max_bitrate=max_bitrate,
        req_format=req_format,
        duration=song.get('length') or 0.0,
        estimate_length=True,
        audio_filters=norm_filter
    )