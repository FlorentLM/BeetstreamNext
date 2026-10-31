# Scrobbling

You can send what you listen to in **BeetstreamNext** to external listening-history services. Well, service. For now only [ListenBrainz](https://listenbrainz.org/) is supported (but Last.fm and Libre.fm are planned).

To scrobble, a user needs the _Scrobbling_ [role](./accounts-and-permissions.md#user-roles) (it is on by default), and the [`external_scrobbling`](../configuration.md#external_scrobbling) setting needs to be enabled server-wide.

## Connecting ListenBrainz

1. On ListenBrainz, copy your **user token** from your [settings page](https://listenbrainz.org/settings/).

2. In **BeetstreamNext**, open your account page (or the admin panel's **Connections** tab) and paste the token in the **Scrobble** card.

## What is sent

| When                                                 | What                                                             |
|------------------------------------------------------|------------------------------------------------------------------|
| A track starts (client sends a "now playing")        | Artist, title, album                                             |
| A track is finished (client sends a scrobble)        | Same as above, plus the time you played it, and the track length |

If the track has MusicBrainz IDs in your Beets library (recording, release, artist), they are sent too, so ListenBrainz can match the track exactly. **BeetstreamNext** also identifies itself as the submitting client.

> [!NOTE]
>
> Radio streams, podcasts, and tracks shorter than 30 seconds are not scrobbled.