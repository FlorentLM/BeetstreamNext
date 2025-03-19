from beetsplug.beetstream.utils import *
from beetsplug.beetstream import app
import flask
from flask import g, request, Response
import xml.etree.cElementTree as ET
from PIL import Image
import io
from random import shuffle

@app.route('/rest/getAlbum', methods=["GET", "POST"])
@app.route('/rest/getAlbum.view', methods=["GET", "POST"])
def get_album():
    res_format = request.values.get('f') or 'xml'
    id = int(album_subid_to_beetid(request.values.get('id')))

    album = g.lib.get_album(id)
    songs = sorted(album.items(), key=lambda song: song.track)

    if (is_json(res_format)):
        res = wrap_res("album", {
            **map_album(album),
            **{ "song": list(map(map_song, songs)) }
        })
        return jsonpify(request, res)
    else:
        root = get_xml_root()
        albumXml = ET.SubElement(root, 'album')
        map_album_xml(albumXml, album)

        for song in songs:
            s = ET.SubElement(albumXml, 'song')
            map_song_xml(s, song)

        return Response(xml_to_string(root), mimetype='text/xml')

@app.route('/rest/getAlbumList', methods=["GET", "POST"])
@app.route('/rest/getAlbumList.view', methods=["GET", "POST"])
def album_list():
    return get_album_list(1)


@app.route('/rest/getAlbumList2', methods=["GET", "POST"])
@app.route('/rest/getAlbumList2.view', methods=["GET", "POST"])
def album_list_2():
    return get_album_list(2)

def get_album_list(version):
    res_format = request.values.get('f') or 'xml'
    # TODO type == 'starred' and type == 'frequent'
    sort_by = request.values.get('type') or 'alphabeticalByName'
    size = int(request.values.get('size') or 10)
    offset = int(request.values.get('offset') or 0)
    from_year = int(request.values.get('fromYear') or 0)
    to_year = int(request.values.get('toYear') or 3000)
    genre = request.values.get('genre')

    albums = list(g.lib.albums())

    if sort_by == 'newest':
        albums.sort(key=lambda a: int(a['added']), reverse=True)
    elif sort_by == 'alphabeticalByName':
        albums.sort(key=lambda a: strip_accents(a['album']).upper())
    elif sort_by == 'alphabeticalByArtist':
        albums.sort(key=lambda a: strip_accents(a['albumartist']).upper())
    elif sort_by == 'alphabeticalByArtist':
        albums.sort(key=lambda a: strip_accents(a['albumartist']).upper())
    elif sort_by == 'recent':
        albums.sort(key=lambda a: a['year'], reverse=True)
    elif sort_by == 'byGenre':
        # albums = list(filter(lambda a: genre.lower() in a['genre'].lower(), albums))
        albums = list(filter(lambda a: genre.lower().strip() in map(str.strip, a['genre'].lower().split(',')), albums))
    elif sort_by == 'byYear':
        albums = list(filter(lambda a: min(from_year, to_year) <= a['year'] <= max(from_year, to_year), albums))
        albums.sort(key=lambda a: (a['year'], a['month'], a['day']), reverse=(from_year > to_year))
    elif sort_by == 'random':
        shuffle(albums)

    albums = handleSizeAndOffset(albums, size, offset)

    if version == 1:
        if (is_json(res_format)):
            return jsonpify(request, wrap_res("albumList", {
                "album": list(map(map_album_list, albums))
            }))
        else:
            root = get_xml_root()
            album_list_xml = ET.SubElement(root, 'albumList')

            for album in albums:
                a = ET.SubElement(album_list_xml, 'album')
                map_album_list_xml(a, album)

            return Response(xml_to_string(root), mimetype='text/xml')

    elif version == 2:
        if (is_json(res_format)):
            return jsonpify(request, wrap_res("albumList2", {
                "album": list(map(map_album, albums))
            }))
        else:
            root = get_xml_root()
            album_list_xml = ET.SubElement(root, 'albumList2')

            for album in albums:
                a = ET.SubElement(album_list_xml, 'album')
                map_album_xml(a, album)

            return Response(xml_to_string(root), mimetype='text/xml')

@app.route('/rest/getGenres', methods=["GET", "POST"])
@app.route('/rest/getGenres.view', methods=["GET", "POST"])
def genres():
    res_format = request.values.get('f') or 'xml'
    with g.lib.transaction() as tx:
        mixed_genres = list(tx.query(
            """
            SELECT genre, COUNT(*) AS n_song, "" AS n_album FROM items GROUP BY genre
            UNION ALL
            SELECT genre, "" AS n_song, COUNT(*) AS n_album FROM albums GROUP BY genre
            """))

    g_dict = {}
    for row in mixed_genres:
        genre_field, n_song, n_album = row
        for key in [g.strip() for g in genre_field.split(',')]:
            if key not in g_dict:
                g_dict[key] = [0, 0]
            if n_song:  # Update song count if present
                g_dict[key][0] += int(n_song)
            if n_album: # Update album count if present
                g_dict[key][1] += int(n_album)

    # And convert to list of tuples (only non-empty genres)
    g_list = [(k, *v) for k, v in g_dict.items() if k]
    # g_list.sort(key=lambda g: strip_accents(g[0]).upper())
    g_list.sort(key=lambda g: g[1], reverse=True)

    if is_json(res_format):
        return jsonpify(request, wrap_res(
            key="genres",
            json={ "genre": [dict(zip(["value", "songCount", "albumCount"], g)) for g in g_list] }
        ))
    else:
        root = get_xml_root()
        genres_xml = ET.SubElement(root, 'genres')

        for genre in g_list:
            genre_xml = ET.SubElement(genres_xml, 'genre')
            genre_xml.text = genre[0]
            genre_xml.set("songCount", str(genre[1]))
            genre_xml.set("albumCount", str(genre[2]))

        return Response(xml_to_string(root), mimetype='text/xml')

@app.route('/rest/getMusicDirectory', methods=["GET", "POST"])
@app.route('/rest/getMusicDirectory.view', methods=["GET", "POST"])
def musicDirectory():
    # Works pretty much like a file system
    # Usually Artist first, than Album, than Songs
    res_format = request.values.get('f') or 'xml'
    id = request.values.get('id')

    if id.startswith(ARTIST_ID_PREFIX):
        artist_id = id
        artist_name = artist_id_to_name(artist_id)
        albums = g.lib.albums(artist_name.replace("'", "\\'"))
        albums = filter(lambda album: album.albumartist == artist_name, albums)

        if (is_json(res_format)):
            return jsonpify(request, wrap_res("directory", {
                "id": artist_id,
                "name": artist_name,
                "child": list(map(map_album, albums))
            }))
        else:
            root = get_xml_root()
            artist_xml = ET.SubElement(root, 'directory')
            artist_xml.set("id", artist_id)
            artist_xml.set("name", artist_name)

            for album in albums:
                a = ET.SubElement(artist_xml, 'child')
                map_album_xml(a, album)

            return Response(xml_to_string(root), mimetype='text/xml')
    elif id.startswith(ALBUM_ID_PREFIX):
        # Album
        id = int(album_subid_to_beetid(id))
        album = g.lib.get_album(id)
        songs = sorted(album.items(), key=lambda song: song.track)

        if (is_json(res_format)):
            res = wrap_res("directory", {
                **map_album(album),
                **{ "child": list(map(map_song, songs)) }
            })
            return jsonpify(request, res)
        else:
            root = get_xml_root()
            albumXml = ET.SubElement(root, 'directory')
            map_album_xml(albumXml, album)

            for song in songs:
                s = ET.SubElement(albumXml, 'child')
                map_song_xml(s, song)

            return Response(xml_to_string(root), mimetype='text/xml')
    elif id.startswith(SONG_ID_PREFIX):
        # Song
        id = int(song_subid_to_beetid(id))
        song = g.lib.get_item(id)

        if (is_json(res_format)):
            return jsonpify(request, wrap_res("directory", map_song(song)))
        else:
            root = get_xml_root()
            s = ET.SubElement(root, 'directory')
            map_song_xml(s, song)

            return Response(xml_to_string(root), mimetype='text/xml')
