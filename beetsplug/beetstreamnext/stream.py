import os
import subprocess
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.utils import (
    FFMPEG_PYTHON, FFMPEG_BIN, ffmpeg, get_mimetype, subsonic_error, sub_to_beets_song
)


def _send_direct(file_path):
    try:
        return flask.send_file(file_path, mimetype=get_mimetype(file_path))
    except OSError:
        return None


def _send_transcode(file_path, start_at: float = 0.0, max_bitrate: int = 128, req_format: str = 'mp3'):

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
            .output('pipe:', format=target['f'], acodec=target['c'], audio_bitrate=f'{max_bitrate}k')
            .run_async(pipe_stdout=True, quiet=True)
        )
    elif FFMPEG_BIN:
        command = ['ffmpeg', '-hide_banner', '-loglevel', 'error']

        if start_at > 0:
            command.extend(["-ss", f"{start_at:.2f}"])

        command.extend([
            '-i', file_path,
            '-vn',  # strip cover art, otherwise many clients just crash
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
                chunk = output_stream.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        except OSError:
            pass
        finally:
            try:
                output_stream.kill()
                output_stream.wait(timeout=10)
            except Exception:
                pass

    response = flask.Response(flask.stream_with_context(generate()), mimetype=target['mime'])

    # # Not sure if it's important to tell clients they cant seek with HTTP range headers
    # response.headers['Accept-Ranges'] = 'none'
    # response.headers['Cache-Control'] = 'no-cache'

    return response


def try_transcode(file_path, start_at: float = 0.0, max_bitrate: int = 128, req_format: str = 'mp3'):
    if FFMPEG_PYTHON or FFMPEG_BIN:
        return _send_transcode(file_path, start_at, max_bitrate, req_format)
    else:
        return _send_direct(file_path)


##
# Endpoints

@app.route('/rest/stream', methods=["GET", "POST"])
@app.route('/rest/stream.view', methods=["GET", "POST"])
def endpoint_stream_song():
    r = flask.request.values

    if not bool(flask.g.user_data.get('streamRole')):
        return subsonic_error(50, resp_fmt=r.get('f', 'xml'))

    max_bitrate = int(r.get('maxBitRate', 0))
    req_format = r.get('format') or 'mp3'
    time_offset = float(r.get('timeOffset', 0.0))

    song_id = sub_to_beets_song(r.get('id'))
    song = flask.g.lib.get_item(song_id)
    song_path = os.fsdecode(song.get('path', b'')) if song else ''

    if song_path:
        song_ext = song_path.rsplit('.', 1)[-1].lower() if '.' in song_path else ''

        needs_transcode = False
        if not app.config['never_transcode'] and req_format != 'raw':

            # Transcode if bitrate too high
            if max_bitrate > 0 and song.get('bitrate', 0) > (max_bitrate * 1000):
                needs_transcode = True

            # or if client wants different format
            elif req_format and req_format != song_ext:
                needs_transcode = True

        if not needs_transcode:
            # send_file handles HTTP 206 Partial Content (Range requests) perfectly
            response = _send_direct(song_path)
        else:
            target_bitrate = max_bitrate if max_bitrate > 0 else 320

            response = try_transcode(
                song_path,
                start_at=time_offset,
                max_bitrate=target_bitrate,
                req_format=req_format
            )

        if response is not None:
            return response

    return subsonic_error(70, resp_fmt=r.get('f', 'xml'))


@app.route('/rest/download', methods=["GET", "POST"])
@app.route('/rest/download.view', methods=["GET", "POST"])
def endpoint_download_song():
    r = flask.request.values

    if not bool(flask.g.user_data.get('downloadRole')):
        return subsonic_error(50, resp_fmt=r.get('f', 'xml'))

    song_id = sub_to_beets_song(r.get('id'))
    item = flask.g.lib.get_item(song_id)

    song_path = os.fsdecode(item.get('path', b'')) if item else ''
    if not song_path:
        return subsonic_error(70, resp_fmt=r.get('f', 'xml'))

    return _send_direct(song_path)
