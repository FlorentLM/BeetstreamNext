from __future__ import annotations
import os
import subprocess
import hashlib
import time
from pathlib import Path
from typing import Generator, Optional, Any, Tuple
import flask

from .. import api_bp

from beetsplug.beetstreamnext.constants import FFMPEG_PYTHON, HLS_CACHE_DIR
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.utils.general import api_bool, send_file
from beetsplug.beetstreamnext.utils.system import get_mimetype, find_binary, resolve_path
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.core.library.health import needs_healing
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.core.media.transcode import FORMAT_MAP, is_lossless, evaluate_limitation, get_normalization_filter, try_transcode
from beetsplug.beetstreamnext.core.library.resolve import Resolve, standardise_datadict


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

    if IDs.decode_type(media_id) == 'podcast_episode':
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

    media_path = str(resolve_path(media_path, app.config['root_directory']))

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

    if IDs.decode_type(media_id) != 'podcast_episode':
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

    # Flagged by a health scan as having decode errors direct-play can't recover from. Checked
    # unconditionally (not just as a last-resort elif) since it changes how any resulting transcode
    # is served, regardless of which other reason also called for one.
    healing = not isinstance(song, dict) and needs_healing(IDs.encode_song(standardise_datadict(song)))

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

    elif healing:
        needs_transcode = True

    if not needs_transcode:
        response = send_file(song_path)
    else:
        target_bitrate = max_bitrate if max_bitrate > 0 else 320

        if req_format != 'raw':
            target_format = req_format
        elif max_bitrate <= 0 and song_ext in FORMAT_MAP:
            # No explicit different-format or bitrate-cap request (e.g. transcoding only to apply
            # ReplayGain or heal bitstream corruption): keep the original container/codec, so a
            # healed flac stays flac instead of silently dropping to lossy mp3.
            target_format = song_ext
        elif max_bitrate <= 0 and is_lossless(song_ext):
            target_format = 'flac'
        else:
            target_format = 'mp3'

        response = try_transcode(
            song_path,
            start_at=time_offset,
            max_bitrate=target_bitrate,
            req_format=target_format,
            duration=song.get('length') or 0.0,
            estimate_length=estimate_length,
            audio_filters=norm_filter,
            exact_length=healing
        )

    if response is not None:
        return response

    song_filename = Path(song_path).name

    if needs_transcode and (FFMPEG_PYTHON or find_binary('ffmpeg')):
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

    healing = not isinstance(item, dict) and needs_healing(IDs.encode_song(standardise_datadict(item)))
    if healing:
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
    can_transcode = bool(find_binary('ffmpeg')) or FFMPEG_PYTHON
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
        # 4th field: whether this transcode is (also) needed to heal bitstream corruption, so
        # getTranscodeStream knows to serve an exact Content-Length instead of an estimated one.
        tx_params = f'{target_container}|{target_br}|{int(bool(norm_filter))}|{int(healing)}'

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

    resolved_type = 'podcast' if IDs.decode_type(media_id) == 'podcast_episode' else 'song'
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
        # container | bitrate | norm | healing
        parts = tx_params_raw.split('|')
        req_format = parts[0]
        max_bitrate = int(float(parts[1]) / 1000) # bps to kbps
        apply_norm = parts[2] == '1'
        healing = parts[3] == '1' if len(parts) > 3 else False
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
        audio_filters=norm_filter,
        exact_length=healing
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

        hls_ffmpeg_bin = find_binary('ffmpeg')
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