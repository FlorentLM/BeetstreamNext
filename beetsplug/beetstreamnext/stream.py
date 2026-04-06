import os
import subprocess
import select
from pathlib import Path

import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.utils import (
    FFMPEG_PYTHON, FFMPEG_BIN, ffmpeg, get_mimetype, subsonic_error, sub_to_beets_song, api_bool, safe_str
)


def _send_direct(file_path):
    try:
        return flask.send_file(file_path, mimetype=get_mimetype(file_path))
    except OSError:
        return None


def _send_transcode(
        file_path,
        start_at: float = 0.0,
        max_bitrate: int = 128,
        req_format: str = 'mp3',
        duration: float = 0.0,
        estimate_length: bool = False
    ):

    format_map = {
        'mp3': {'f': 'mp3', 'c': 'libmp3lame', 'mime': 'audio/mpeg'},
        'ogg': {'f': 'ogg', 'c': 'libvorbis', 'mime': 'audio/ogg'},
        'opus': {'f': 'ogg', 'c': 'libopus', 'mime': 'audio/ogg'},
        'aac': {'f': 'adts', 'c': 'aac', 'mime': 'audio/aac'},
        'm4a': {'f': 'adts', 'c': 'aac', 'mime': 'audio/aac'},
        'flac': {'f': 'flac', 'c': 'flac', 'mime': 'audio/flac'}
    }

    target = format_map.get(req_format.lower() if req_format else 'mp3', format_map['mp3'])

    if FFMPEG_PYTHON:
        input_stream = ffmpeg.input(file_path, ss=start_at) if start_at > 0 else ffmpeg.input(file_path)

        output_stream = (
            input_stream
            .audio
            .output(
                'pipe:',
                format=target['f'],
                acodec=target['c'],
                audio_bitrate=f'{max_bitrate}k',
                map_metadata='-1'
            )
            .run_async(pipe_stdout=True, quiet=True)
        )
    elif FFMPEG_BIN:
        command = ['ffmpeg', '-hide_banner', '-loglevel', 'error']

        if start_at > 0:
            command.extend(["-ss", f"{start_at:.2f}"])

        command.extend([
            '-i', file_path,
            '-vn',  # strip cover art, otherwise many clients just crash
            '-map_metadata', '-1',
            '-f', target['f'],
            '-c:a', target['c'],
            '-b:a', f'{max_bitrate}k',
            'pipe:1'
        ])
        output_stream = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    else:
        return None

    def generate():
        try:
            while True:
                ready, _, _ = select.select([output_stream.stdout], [], [], 2.0)

                if ready:
                    chunk = output_stream.stdout.read(8192)
                    if not chunk:
                        break
                    yield chunk
                else:
                    if output_stream.poll() is not None:
                        break
        except OSError:
            pass
        finally:
            try:
                output_stream.terminate()
                try:
                    output_stream.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    output_stream.kill()
                    output_stream.wait(timeout=5)
            except Exception:
                pass

    response = flask.Response(flask.stream_with_context(generate()), mimetype=target['mime'])

    if estimate_length and max_bitrate > 0 and duration > 0:
        # This is an estimate. Pretty sure it will be very inaccurate in many cases.
        # but as per the spec:
        #   "Content-Length HTTP header will be set to an estimated value for transcoded or downsampled media."
        # so... yeah
        remaining = max(0.0, duration - start_at)
        estimated_bytes = int((max_bitrate * 1000 / 8) * remaining)
        response.headers['Content-Length'] = estimated_bytes

    # TODO: Not sure if it's important to tell clients they cant seek with HTTP range headers?
    # response.headers['Accept-Ranges'] = 'none'
    # response.headers['Cache-Control'] = 'no-cache'

    return response


def try_transcode(
        file_path,
        start_at: float = 0.0,
        max_bitrate: int = 128,
        req_format: str = 'mp3',
        duration: float = 0.0,
        estimate_length: bool = False
    ):

    if FFMPEG_PYTHON or FFMPEG_BIN:
        return _send_transcode(
            file_path=file_path,
            start_at=start_at,
            max_bitrate=max_bitrate,
            req_format=req_format,
            duration=duration,
            estimate_length=estimate_length
        )

    else:
        return _send_direct(file_path)


##
# Endpoints

# Spec: https://opensubsonic.netlify.app/docs/endpoints/stream/
@app.route('/rest/stream', methods=["GET", "POST"])
@app.route('/rest/stream.view', methods=["GET", "POST"])
def endpoint_stream_song():
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

    beets_song_id = sub_to_beets_song(song_id)
    song = flask.g.lib.get_item(beets_song_id)
    song_path = os.fsdecode(song.get('path', b'')) if song else ''

    if song_path:
        song_ext = song_path.rsplit('.', 1)[-1].lower() if '.' in song_path else ''

        needs_transcode = False

        # Transcode if bitrate too high
        if max_bitrate > 0 and song.get('bitrate', 0) > (max_bitrate * 1000):
            needs_transcode = True

        # or if client wants different format
        elif req_format != 'raw' and req_format != song_ext and not app.config['never_transcode']:
            needs_transcode = True

        if not needs_transcode:
            response = _send_direct(song_path)
        else:
            target_bitrate = max_bitrate if max_bitrate > 0 else 320

            return try_transcode(
                song_path,
                start_at=time_offset,
                max_bitrate=target_bitrate,
                req_format=req_format if req_format != 'raw' else 'mp3',
                duration=song.get('length') or 0.0,
                estimate_length=estimate_length
            )

        if response is not None:
            return response

        app.logger.warning(f"Direct play of song '{Path(song_path).name}' failed.")

    return subsonic_error(70, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/download/
@app.route('/rest/download', methods=["GET", "POST"])
@app.route('/rest/download.view', methods=["GET", "POST"])
def endpoint_download_song():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    song_id = r.get('id', default='', type=safe_str)         # Required

    if not bool(flask.g.user_data.get('downloadRole')):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not song_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    beets_song_id = sub_to_beets_song(song_id)
    item = flask.g.lib.get_item(beets_song_id)

    song_path = os.fsdecode(item.get('path', b'')) if item else ''
    if not song_path:
        return subsonic_error(70, resp_fmt=resp_fmt)

    return _send_direct(song_path)
