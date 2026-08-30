import flask

from .. import public_bp

from beetsplug.beetstreamnext.core.mappings import Resolve
from beetsplug.beetstreamnext.public.tokeniser import stream_tokeniser, image_tokeniser
from beetsplug.beetstreamnext.utils.general import send_file
from beetsplug.beetstreamnext.core.images import send_album_art, send_artist_image


@public_bp.route('/tokenised-stream/<token>/<filename>')
def tokenised_stream(token: str, filename: str) -> flask.Response:
    """
    Unauthenticated but token-gated stream route. Used by Sonos jukebox backend.

    Note: The 'filename' arg is only there for backends that need a recognisable extension in the URL
    (Sonos rejects AddURIToQueue with UPnP error 804 on an extension-less URL), but the token is sufficient
    to resolve the file.
    """
    path = stream_tokeniser.resolve(token)
    if not path:
        flask.abort(404)

    response = send_file(path)
    if response is None:
        flask.abort(404)
    return response


@public_bp.route('/tokenised-image/<token>.jpg')
def tokenised_image(token: str) -> flask.Response:
    """
    Unauthenticated but token-gated image route. Used for OpenSubsonic responses that contain
    image URLs which the spec treats as plain external image links that a client can fetch directly (*).

    Note: The '.jpg' suffix is only there for some clients that expect a recognisable image
    extension in the URL, same as 'tokenised_stream' above), and tokens otherwise never contain a '.' anyway.

    The token alone resolves the artist/album and the image size.
    """

    payload = image_tokeniser.resolve(token)
    if not payload:
        flask.abort(404)

    subsonic_id, _, size = payload.partition('|')
    size = int(size) if size else None

    entry_type, entry = Resolve.any(subsonic_id)

    response = None
    if entry_type == 'album' and entry:
        response = send_album_art(entry.id, size=size)

    elif entry_type == 'artist':
        response = send_artist_image(subsonic_id, size=size)

    return response if response is not None else flask.abort(404)


# (*) The responses are: artistInfo, artistInfo2, albumInfo and ArtistID3, from endpoints: getArtistInfo2, getArtistInfo, getAlbumInfo2, getAlbumInfo