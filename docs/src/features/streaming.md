# Streaming & audio quality

## Direct play & transcoding

BeetstreamNext serves files directly if a client requests them as-is, or transcodes on the fly via [FFmpeg](https://ffmpeg.org/) otherwise (bitrate limit requested by the client, unsupported format, etc.). 

You can set `never_transcode` to always stream the original file regardless of what the client asks for.

Direct play means fully lossless, bit-perfect streaming: FLAC, ALAC, and other lossless formats are sent to the client exactly as they sit in your library.

## Adaptive-bitrate HLS

For clients that request it, BeetstreamNext supports OpenSubsonic's [HLS](https://opensubsonic.netlify.app/docs/endpoints/hls/) spec and can serve AAC HLS with adaptive bitrate, allowing the client's player switch quality on the fly as network conditions change.

## ReplayGain

BeetstreamNext can apply ReplayGain normalization server-side during transcoding, independent of whether the client itself supports ReplayGain or not:

- `replaygain_enabled` turns this on.

- `replaygain_preamp` adds extra gain (dB) on top of the tag value.

- `replaygain_fallback` is the gain applied to tracks that have no ReplayGain tag in Beets.

- `audio_peak_limit` prevents the result from clipping (peaks capped at 0 dB) regardless of the gain applied.

## Health scan & healed streams

A background scan keeps a list of eventual problems found in your library. It runs periodically, and can also be started on demand from the web UI.

There are two sorts of findings:

### Broken files

| Issue             | Flagged when                                                |
|-------------------|-------------------------------------------------------------|
| **Missing file**  | The file Beets has listed in its database can't be found    |
| **Decode errors** | File can't be read, or reports corruption while decoding it |

A song flagged with decode errors is automatically routed through a transcode pass instead of direct play. This healing pass keeps the original container/codec (when the client doesn't ask for something else), so a lossless file is re-encoded _losslessly_, but without the corruption.

### Library quality

**BeetstreamNext** doesn't edit your Beets library, if you want to fix them you should do it in in Beets (`beet ls`, `beet modify`, `beet remove`, etc).

| Issue                 | Flagged when                                                                                                                  |
|-----------------------|-------------------------------------------------------------------------------------------------------------------------------|
| **Duplicate**         | Songs share a recording ID, or the same artist, title and length                                                              |
| **Same recording**    | Songs with different recording IDs share an AcoustID or identical fingerprint (lists each copy's codec and bitrate)           |
| **Lossy copy**        | An album is entirely lossy, and an album with the same artist and title is entirely lossless                                  |
| **Inconsistent tags** | Tracks of one album disagree on the year, album title, album artist, etc                                                      |
| **Missing tracks**    | A disc has non contiguous track numbers                                                                                       |
| **Mixed releases**    | Tracks of one album carry different release IDs (typically a multi-disc album where discs were matched to different releases) |
| **Artist mismatch**   | None of an album's track artist IDs is its album artist ID (except for compilations)                                          |
| **Missing tags**      | A track has no title, artist, album, album artist or track number                                                             |
| **No ID**             | A track has neither a `mb_trackid` nor a `mb_releasetrackid`                                                                  |
| **No fingerprint**    | A track has no AcoustID data, so it couldn't be checked for same recordings (only listed once some tracks have it)            |

> [!NOTE]
> Findings about a whole album are listed on its first track.
> 
> **Same recording** and **No fingerprint** checks read fields written by the Beets [`chroma`](https://beets.readthedocs.io/en/stable/plugins/chroma.html) plugin.

## Reverse-proxy file offloading

Direct (non-transcoded) file serving can be offloaded to the reverse proxy instead of streaming bytes through the Python process, via `X-Accel-Redirect` (Nginx) or `X-Sendfile` (Apache).

See [Reverse proxy & CORS](../reverse-proxy.md) for how to setup.

---

See the [configuration reference](../configuration.md) for any of the settings mentioned here.
