# Streaming & audio quality

## Direct play & transcoding

BeetstreamNext serves files directly when a client can play them as-is, and transcodes on the fly via [FFmpeg](https://ffmpeg.org/) otherwise (bitrate limit requested by the client, unsupported format, etc.). You can set `never_transcode` to always stream the original file regardless of what the client asks for.

Direct play means fully lossless, bit-perfect streaming: FLAC, ALAC, and other lossless formats are sent to the client byte-for-byte exactly as they sit in your library.

## Adaptive-bitrate HLS

For clients that request it, BeetstreamNext supports OpenSubsonic's [HLS](https://opensubsonic.netlify.app/docs/endpoints/hls/) spec and can serve AAC HLS with adaptive bitrate, allowing the client's player switch quality on the fly as network conditions change.

## ReplayGain

BeetstreamNext can apply ReplayGain normalization server-side during transcoding, independent of whether the client itself supports ReplayGain or not:

- `replaygain_enabled` turns this on.
- `replaygain_preamp` adds extra gain (dB) on top of the tag value.
- `replaygain_fallback` is the gain applied to tracks that have no ReplayGain tag in Beets.
- `audio_peak_limit` prevents the result from clipping (peaks capped at 0 dB) regardless of the gain applied.

## Encoding errors detection & self-healing streams

A background scan (also triggerable on-demand from the admin panel) probes files for decode errors. If a track is flagged, it is automatically routed through a transcode pass instead of direct play, so a corrupt/broken file doesn't just fail to play.

This healing pass keeps the original container/codec (when the client doesn't ask for something else): a flagged FLAC or ALAC file is re-encoded through FFmpeg's decoder rather than streamed byte-for-byte, but the output stays lossless.

## Reverse-proxy file offloading

Direct (non-transcoded) file serving can be offloaded to the reverse proxy instead of streaming bytes through the Python process, via `X-Accel-Redirect` (Nginx) or `X-Sendfile` (Apache). See [Reverse proxy & CORS](../reverse-proxy.md)) for how to setup.

---

See the [configuration reference](../configuration.md) for any of the settings mentioned here.
