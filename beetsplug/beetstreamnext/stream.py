from beetsplug.beetstreamnext.utils import *
import subprocess
import flask

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
        command = [
            "ffmpeg",
            f"-ss {start_at:.2f}" if start_at else "",
            "-i", file_path,
            "-f", "mp3",
            "-b:a", f"{max_bitrate}k",
            "pipe:1"
        ]
        output_stream = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    else:
        return None

    return flask.Response(output_stream.stdout, mimetype='audio/mpeg')


def try_transcode(file_path, start_at: float = 0.0, max_bitrate: int = 128):
    if have_ffmpeg:
        return transcode(file_path, start_at, max_bitrate)
    else:
        return direct(file_path)