from beetsplug.beetstream.utils import *
import subprocess
import flask

have_ffmpeg = FFMPEG_PYTHON or FFMPEG_BIN


def direct(filePath):
    return flask.send_file(filePath, mimetype=get_mimetype(filePath))

def transcode(filePath, maxBitrate):
    if FFMPEG_PYTHON:
        output_stream = (
            ffmpeg
            .input(filePath)
            .audio
            .output('pipe:', format="mp3", audio_bitrate=maxBitrate * 1000)
            .run_async(pipe_stdout=True, quiet=True)
        )
    elif FFMPEG_BIN:
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

def try_transcode(filePath, maxBitrate):
    if have_ffmpeg:
        return transcode(filePath, maxBitrate)
    else:
        return direct(filePath)
