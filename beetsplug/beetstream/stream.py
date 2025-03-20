from beetsplug.beetstream.utils import path_to_content_type
import flask
import shutil
import importlib

ffmpeg_bin = shutil.which("ffmpeg") is not None
ffmpeg_python = importlib.util.find_spec("ffmpeg") is not None

if ffmpeg_python:
    import ffmpeg
elif ffmpeg_bin:
    import subprocess

have_ffmpeg = ffmpeg_python or ffmpeg_bin


def direct(filePath):
    return flask.send_file(filePath, mimetype=path_to_content_type(filePath))

def transcode(filePath, maxBitrate):
    if ffmpeg_python:
        output_stream = (
            ffmpeg
            .input(filePath)
            .audio
            .output('pipe:', format="mp3", audio_bitrate=maxBitrate * 1000)
            .run_async(pipe_stdout=True, quiet=True)
        )
    elif ffmpeg_bin:
        command = [
            "ffmpeg",
            "-i", filePath,
            "-f", "mp3",
            "-b:a", f"{maxBitrate}k",
            "pipe:1"
        ]
        output_stream = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    else:
        raise RuntimeError("Can't transcode, ffmpeg is not available.")

    return flask.Response(output_stream.stdout, mimetype='audio/mpeg')

def try_to_transcode(filePath, maxBitrate):
    if have_ffmpeg:
        return transcode(filePath, maxBitrate)
    else:
        return direct(filePath)
