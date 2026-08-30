import os
import subprocess
import math
import hashlib
import time
from pathlib import Path
import queue
import threading
from typing import Generator, Optional, Any, Tuple
import flask

from .. import api_bp

from beetsplug.beetstreamnext.constants import FFMPEG_PYTHON, HLS_CACHE_DIR
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.utils.general import api_bool, send_file
from beetsplug.beetstreamnext.utils.system import get_mimetype, find_ffmpeg
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.api.idmapper import IDs, Resolve

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


def _get_media_context(req_values, required_role='streamRole') -> Tuple[Optional[Any], Optional[str], Optional[flask.Response]]:
    """Helper to check permissions, IDs, and retrieve absolute track path (song, or a downloaded podcast episode)."""

    resp_fmt = req_values.get('f', default='xml', type=safe_str)
    media_id = req_values.get('id', default='', type=safe_str)      # Required

    if not bool(flask.g.user_data.get(required_role)):
        return None, None, subsonic_error(50, resp_fmt=resp_fmt)

    if not media_id:
        if required_role == 'streamRole':
            media_id = req_values.get('mediaId', default='', type=safe_str)     # Required in getTranscodeDecision / getTranscodeStream

        if not media_id:
            return None, None, subsonic_error(10, resp_fmt=resp_fmt)

    if IDs.decode_type(media_id) == 'episode':
        episode = Resolve.podcast_episode(media_id)
        if not episode or episode.get('status') != 'completed' or not episode.get('file_path'):
            return None, None, subsonic_error(70, resp_fmt=resp_fmt)

        episode_path = episode['file_path']
        if not os.path.isfile(episode_path):
            return None, None, subsonic_error(70, resp_fmt=resp_fmt)

        episode['length'] = episode.get('duration') or 0.0     # alias expected by the rest of this module
        return episode, episode_path, None

    media = Resolve.song(media_id)
    if not media:
        return None, None, subsonic_error(70, resp_fmt=resp_fmt)

    media_path = os.fsdecode(media.get('path', b''))
    if not media_path:
        return None, None, subsonic_error(70, resp_fmt=resp_fmt)

    path_obj = Path(media_path)
    if not path_obj.is_absolute():
        media_path = str(app.config['root_directory'] / path_obj)

    return media, media_path, None


def _streamdownload_podcast(req_values, required_role: str) -> flask.Response | None:
    """
    Some clients never call downloadPodcastEpisode, they just hit /stream (or /download)
    directly with an episode id and expect audio back...

    So for episodes not yet downloaded, relayed_download() proxies it live,
    and simultaneously saves it to local storage.

    Returns None when this doesn't apply (not a podcast episode id, or already downloaded): in this case
    _get_media_context handles it (it supports range/transcode) so the caller falls through just fine.
    """

    resp_fmt = req_values.get('f', default='xml', type=safe_str)
    media_id = req_values.get('id', default='', type=safe_str)
    if not media_id and required_role == 'streamRole':
        media_id = req_values.get('mediaId', default='', type=safe_str)

    if IDs.decode_type(media_id) != 'episode':
        return None

    if not bool(flask.g.user_data.get(required_role)):
        return subsonic_error(50, resp_fmt=resp_fmt)

    episode = Resolve.podcast_episode(media_id)
    if not episode:
        return subsonic_error(70, resp_fmt=resp_fmt)

    if episode.get('status') == 'completed' and episode.get('file_path'):
        return None   # already on disk, _get_media_context serves it normally

    if not episode.get('audio_url'):
        return subsonic_error(70, resp_fmt=resp_fmt)

    podcast_manager = flask.g.podcast_manager

    if podcast_manager.is_downloading(episode['id']):
        return subsonic_error(0, message='This episode is already being fetched, try again shortly.', resp_fmt=resp_fmt)

    try:
        started = podcast_manager.relayed_download(episode['id'], episode['channel_id'], episode['audio_url'])
    except Exception as e:
        bsn_logger.warning(f"Failed to start streaming podcast episode {episode['id']}: {e}")
        return subsonic_error(0, message=f'Failed to fetch episode audio: {e}', resp_fmt=resp_fmt)

    if started is None:
        return subsonic_error(0, message='This episode is already being fetched, try again shortly.', resp_fmt=resp_fmt)

    resp, tmp_path, target_path = started
    mimetype = resp.headers.get('Content-Type') or get_mimetype(str(target_path))
    episode_id = episode['id']

    def generate() -> Generator:
        size = 0
        success = False
        try:
            with open(tmp_path, 'wb') as f:
                for chunk in resp.iter_content(65536):
                    if not chunk:
                        continue
                    f.write(chunk)
                    size += len(chunk)
                    yield chunk
            success = True
        except Exception as e:
            bsn_logger.warning(f'Streaming podcast episode {episode_id} failed: {e}')
        finally:
            podcast_manager.finish_relayed_download(episode_id, tmp_path, target_path, size, success)

    response = flask.Response(flask.stream_with_context(generate()), mimetype=mimetype)
    response.headers['Accept-Ranges'] = 'none'
    return response


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
    ffmpeg_bin = find_ffmpeg()

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


def try_transcode(
        file_path: str | Path,
        start_at: float = 0.0,
        max_bitrate: int = 128,
        req_format: str = 'mp3',
        duration: float = 0.0,
        estimate_length: bool = False,
        audio_filters: Optional[str] = None
    ) -> flask.Response | None:

    if FFMPEG_PYTHON or find_ffmpeg():
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
        return send_file(file_path)


##
# Endpoints

# Spec: https://opensubsonic.netlify.app/docs/endpoints/stream/
@api_bp.route('/stream', methods=['GET', 'POST'])
@api_bp.route('/stream.view', methods=['GET', 'POST'])
def endpoint_stream_song() -> flask.Response | None:
    r = flask.request.values

    live_response = _streamdownload_podcast(r, 'streamRole')
    if live_response is not None:
        return live_response

    song, song_path, err_resp = _get_media_context(r, 'streamRole')
    if err_resp:
        return err_resp

    resp_fmt = r.get('f', default='xml', type=safe_str)
    max_bitrate = r.get('maxBitRate', default=0, type=int)
    req_format = r.get('format', default='raw', type=safe_str)
    time_offset = r.get('timeOffset', default=0.0, type=float)
    estimate_length = r.get('estimateContentLength', default=False, type=api_bool)

    user_max_bitrate = flask.g.user_data.get('maxBitRate', 0)
    if user_max_bitrate > 0:
        max_bitrate = min(user_max_bitrate, max_bitrate) if max_bitrate > 0 else user_max_bitrate

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
        response = send_file(song_path)
    else:
        target_bitrate = max_bitrate if max_bitrate > 0 else 320

        response = try_transcode(
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

    song_filename = Path(song_path).name

    if needs_transcode and (FFMPEG_PYTHON or find_ffmpeg()):
        bsn_logger.warning(f"Transcode of song '{song_filename}' failed.")
        return subsonic_error(0, message='Transcoding failed.', resp_fmt=resp_fmt)

    bsn_logger.warning(f"Direct play of song '{song_filename}' failed.")
    return subsonic_error(70, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/download/
@api_bp.route('/download', methods=['GET', 'POST'])
@api_bp.route('/download.view', methods=['GET', 'POST'])
def endpoint_download_song() -> flask.Response | None:
    r = flask.request.values

    live_response = _streamdownload_podcast(r, 'downloadRole')
    if live_response is not None:
        return live_response

    song, song_path, err_resp = _get_media_context(r, 'downloadRole')
    if err_resp:
        return err_resp

    response = send_file(song_path)
    if response is not None:
        return response

    resp_fmt = r.get('f', default='xml', type=safe_str)
    bsn_logger.warning(f"Download of song '{Path(song_path).name}' failed.")
    return subsonic_error(70, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/gettranscodedecision/
@api_bp.route('/getTranscodeDecision', methods=['POST'])
@api_bp.route('/getTranscodeDecision.view', methods=['POST'])
def endpoint_get_transcode_decision() -> flask.Response:
    r = flask.request.values

    item, song_path, err_resp = _get_media_context(r, 'streamRole')
    if err_resp:
        return err_resp

    resp_fmt = r.get('f', default='xml', type=safe_str)
    client_info = flask.request.get_json(silent=True) or {}

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
    can_transcode = bool(find_ffmpeg()) or FFMPEG_PYTHON
    transcode_stream = None
    tx_params = ''

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
@api_bp.route('/getTranscodeStream', methods=['GET', 'POST'])
@api_bp.route('/getTranscodeStream.view', methods=['GET', 'POST'])
def endpoint_get_transcode_stream() -> flask.Response | None:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    media_id = r.get('id', default='', type=safe_str) or r.get('mediaId', default='', type=safe_str)
    media_type = r.get('mediaType', default='', type=safe_str).lower()

    if media_type and media_type not in ('song', 'podcast'):
        return subsonic_error(0, message="'mediaType' must be 'song' or 'podcast'.", resp_fmt=resp_fmt)

    resolved_type = 'podcast' if IDs.decode_type(media_id) == 'episode' else 'song'
    if media_type and media_type != resolved_type:
        return subsonic_error(0, message=f"'mediaType' ({media_type}) does not match the resolved media ({resolved_type}).", resp_fmt=resp_fmt)

    song, song_path, err_resp = _get_media_context(r, 'streamRole')
    if err_resp:
        return err_resp

    offset = r.get('offset', default=0.0, type=float)
    tx_params_raw = r.get('transcodeParams', default='', type=str)

    if not tx_params_raw:
        return subsonic_error(10, resp_fmt=resp_fmt)

    try:
        # container | bitrate | norm
        parts = tx_params_raw.split('|')
        req_format = parts[0]
        max_bitrate = int(float(parts[1]) / 1000) # bps to kbps
        apply_norm = parts[2] == '1'
    except (IndexError, ValueError):
        return subsonic_error(0, 'Invalid transcodeParams', resp_fmt=resp_fmt)

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


# Spec: https://opensubsonic.netlify.app/docs/endpoints/hls/
@api_bp.route('/hls', methods=['GET', 'POST'])
@api_bp.route('/hls.view', methods=['GET', 'POST'])
@api_bp.route('/hls.m3u8', methods=['GET', 'POST'])
def endpoint_hls() -> flask.Response | None:
    r = flask.request.values

    song, song_path, err_resp = _get_media_context(r, 'streamRole')
    if err_resp:
        return err_resp

    resp_fmt = r.get('f', default='xml', type=safe_str)

    bitrates_raw = r.getlist('bitRate', type=safe_str)
    bitrates = []
    for br_raw in bitrates_raw:
        try:
            # Handle standard (bitRate=128) and Video format (bitRate=1000@480x360)
            br = int(br_raw.split('@')[0])
            if br > 0: bitrates.append(br)
        except ValueError:
            pass

    if not bitrates:
        max_br = r.get('maxBitRate', default=0, type=int)
        bitrates = [max_br if max_br > 0 else 160]

    # Cap at user max bitrate
    user_max_bitrate = flask.g.user_data.get('maxBitRate', 0)
    if user_max_bitrate > 0:
        bitrates = [min(br, user_max_bitrate) for br in bitrates]

    bitrates = sorted(list(set(bitrates)))  # dedup and sort

    try:
        mtime = os.path.getmtime(song_path)
    except OSError:
        mtime = 0.0

    # Unique hash for this file + bitrates combination
    stream_id = hashlib.md5(f"{song.id}_{'-'.join(map(str, bitrates))}_{mtime}".encode()).hexdigest()
    stream_dir = HLS_CACHE_DIR / stream_id

    # If this specific one doesn't exist build it
    if not stream_dir.exists():
        stream_dir.mkdir(parents=True)

        hls_ffmpeg_bin = find_ffmpeg()
        if not (hls_ffmpeg_bin or FFMPEG_PYTHON):
            return subsonic_error(0, message='FFmpeg is required for HLS streaming.', resp_fmt=resp_fmt)

        norm_filter = get_normalization_filter(song)

        command = [
            hls_ffmpeg_bin or 'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-i', str(song_path)
        ]

        if norm_filter:
            command.extend(['-af', norm_filter])

        # Spawn a stream for each requested bitrate
        for i, br in enumerate(bitrates):
            br_dir = stream_dir / str(br)
            br_dir.mkdir(exist_ok=True)

            command.extend([
                '-map', '0:a',
                f'-b:a:{i}', f'{br}k',
                f'-c:a:{i}', 'aac',  # HLS expects AAC or mp3
                '-f', 'hls',
                '-hls_time', '10',  # 10 second chunks
                '-hls_list_size', '0',  # keep all chunks
                '-hls_playlist_type', 'event',  # tells player that chunks will keep arriving
                '-hls_segment_filename', str(br_dir / '%03d.ts'),
                '-hls_base_url', f'hls_data/{stream_id}/{br}/', # tells client where to request the chunks from
                str(br_dir / 'index.m3u8')
            ])

        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # generate and return a playlist pointing to the subplaylists, client will call /hls_data/.../index.m3u8
    master_playlist = ['#EXTM3U']
    for br in bitrates:
        master_playlist.append(f'#EXT-X-STREAM-INF:BANDWIDTH={br * 1000},CODECS="mp4a.40.2"')
        master_playlist.append(f'hls_data/{stream_id}/{br}/index.m3u8')

    master_playlist_str = "\n".join(master_playlist) + '\n'
    return flask.Response(master_playlist_str, mimetype='application/vnd.apple.mpegurl')


@api_bp.route('/hls_data/<stream_id>/<bitrate>/<filename>')
def endpoint_hls_data(stream_id: str, bitrate: str, filename: str) -> flask.Response:

    if not flask.g.user_data.get('streamRole'):
        flask.abort(403)

    if not stream_id.isalnum() or not bitrate.isdigit() or not (filename.endswith('.ts') or filename.endswith('.m3u8')):
        flask.abort(400)

    target_path = HLS_CACHE_DIR / stream_id / bitrate / filename

    # ffmpeg might still be generating the index.m3u8 or the first .ts chunk, so wait for it if needed
    timeout = 10.0
    start = time.time()
    while not target_path.exists() and (time.time() - start < timeout):
        time.sleep(0.25)

    if not target_path.exists():
        flask.abort(404)

    mimetype = 'application/vnd.apple.mpegurl' if filename.endswith('.m3u8') else 'video/MP2T'
    return flask.send_file(target_path, mimetype=mimetype)