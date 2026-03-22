import os
import subprocess
import flask

from beetsplug.beetstreamnext.utils import FFMPEG_PYTHON, FFMPEG_BIN, ffmpeg, get_mimetype

have_ffmpeg = FFMPEG_PYTHON or FFMPEG_BIN


def direct(file_path):
    if os.path.isfile(file_path):
        return flask.send_file(file_path, mimetype=get_mimetype(file_path))
    else:
        return None


def transcode(file_path, start_at: float = 0.0, max_bitrate: int = 128):
    if FFMPEG_PYTHON:
        input_stream = ffmpeg.input(file_path, ss=start_at) if start_at else ffmpeg.input(file_path)

        output_stream = (
            input_stream
            .audio
            .output('pipe:', format="mp3", audio_bitrate=max_bitrate * 1000)
            .run_async(pipe_stdout=True, quiet=True)
        )
    elif FFMPEG_BIN:
        command = ["ffmpeg"]

        if start_at > 0:
            command.extend(["-ss", f"{start_at:.2f}"])

        command.extend([
            "-i", file_path,
            "-vn",
            "-f", "mp3",
            "-b:a", f"{max_bitrate}k",
            "pipe:1"
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
        finally:
            try:
                output_stream.kill()
                output_stream.wait(timeout=10)
            except Exception:
                pass

    return flask.Response(generate(), mimetype='audio/mpeg')


def try_transcode(file_path, start_at: float = 0.0, max_bitrate: int = 128):
    if have_ffmpeg:
        return transcode(file_path, start_at, max_bitrate)
    else:
        return direct(file_path)