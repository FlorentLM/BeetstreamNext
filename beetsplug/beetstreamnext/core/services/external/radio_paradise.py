from __future__ import annotations


def get_qualities() -> dict:
    """Radio Paradise qualities."""
    return {
        '320': 'AAC 320k',
        'flac': 'FLAC',
        '192': 'MP3 192k',
        '128': 'AAC 128k',
        '64': 'AAC 64k',
    }


def get_channels() -> list[dict]:
    """Radio Paradise channels with their stream URL for each quality."""

    channel_titles = [
        (0, 'Main Mix', 'main-mix'),
        (1, 'Mellow Mix', 'mellow'),
        (2, 'RockIt!', 'rock'),
        (3, 'The Globe', 'global'),
        (5, 'Beyond...', 'beyond'),
        (9, 'Velvet', 'velvet'),
        (42, 'Serenity', 'serenity'),
        (945, 'KFAT', 'kfat'),
    ]

    main_mix_streams = {'flac': 'flac', '320': 'aac-320', '192': 'mp3-192', '128': 'aac-128', '64': 'aac-64'}

    channels = []

    for chan, title, slug in channel_titles:
        streams = {}

        for quality in get_qualities():
            if chan == 0:
                stream = main_mix_streams[quality]
            elif slug == 'serenity':
                # Serenity only has FLAC and 64 AAC
                stream = 'serenity-flac' if quality == 'flac' else 'serenity'
            else:
                stream = f'{slug}-{quality}'
            streams[quality] = f'https://stream.radioparadise.com/{stream}'

        channels.append({
            'title': title,
            'name': f'Radio Paradise - {title}',
            'homepage_url': 'https://radioparadise.com',
            'favicon': f'https://img.radioparadise.com/channels/0/{chan}/cover_512x512/0.jpg',
            'streams': streams,
        })

    return channels
