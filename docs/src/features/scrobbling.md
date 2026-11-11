# Scrobbling

You can send what you listen to in **BeetstreamNext** to external listening-history services. Supported services are [ListenBrainz](https://listenbrainz.org/), [Last.fm](https://www.last.fm/) and [Libre.fm](https://libre.fm/).

To scrobble, a user needs the _Scrobbling_ [role](./accounts-and-permissions.md#user-roles) (it is on by default), and the [`external_scrobbling`](../configuration.md#external_scrobbling) setting needs to be enabled server-wide.

## Connecting ListenBrainz

1. On ListenBrainz, copy your **user token** from your [settings page](https://listenbrainz.org/settings/).

2. In **BeetstreamNext**, open your account page (or the admin panel's **Connections** tab) and paste the token in the **Scrobble** card.

## Connecting Last.fm or Libre.fm

These need the [`lastfm_api_key`](../configuration.md#lastfm_api_key) and the [`lastfm_shared_secret`](../configuration.md#lastfm_shared_secret) settings, set by an admin. Libre.fm uses the same ones.

> [!NOTE]
>
> Last.fm only shows the shared secret once when the API key is created. If you didn't save it, [create a new API key](https://www.last.fm/api/account/create) (leave the callback URL empty) and use its new key and secret.

**Each user:**

1. In the **Scrobble** card, click **Connect** on the service.

2. Click **Open Last.fm**, and approve access to your account on their site.

3. Come back to **BeetstreamNext** and click **I've approved it**.

Your password is never seen by **BeetstreamNext**, only the session key returned by Last.fm is stored (encrypted).

## Using multi-scrobbler

[multi-scrobbler](https://github.com/FoxxMD/multi-scrobbler) can collect your listens and forward them to many services at once (Last.fm, Maloja, Koito, etc.). There are two ways to use it with **BeetstreamNext**:

- **ListenBrainz endpoint ([recommended](https://docs.multi-scrobbler.app/quickstart/#tab-panel-0-2)):** Using this, **BeetstreamNext** can send scrobbles to multi-scrobbler with exact play times.

  1. In multi-scrobbler, create a _ListenBrainz endpoint_ source. With Docker, add these to the `environment` section of its compose file. The token can be any secret string:

     ```yaml
     environment:
       LZE_TOKEN: myCoolToken
       LZE_ID: BeetstreamNextLZ
     ```

  2. In _Connections_ tab > _Scrobble_ card in **BeetstreamNext**, set the ListenBrainz URL to your multi-scrobbler address, for example `http://multi-scrobbler:9078`, and use the token you defined in step 1.

- **Subsonic source:** in multi-scrobbler, add a [_Subsonic_ source](https://docs.multi-scrobbler.app/docs/configuration/sources/subsonic/) pointing at your **BeetstreamNext** server with your username and an [app password](./app-passwords.md). Nothing to set up here, but multi-scrobbler works by polling the "now playing" endpoint, so play times are less precise.

> [!WARNING]
>
> If multi-scrobbler receives your listens from **BeetstreamNext** and forwards them to ListenBrainz or Last.fm, don't also connect these services directly in the Scrobble card.

> [!IMPORTANT]
>
> With the ListenBrainz endpoint on a self-hosted multi-scrobbler instance, you need to allow **BeetstreamNext** to reach it on your private network: see [Services on your local network](./security.md#services-on-your-local-network).

## What is sent

| When                                                 | What                                                             |
|------------------------------------------------------|------------------------------------------------------------------|
| A track starts (client sends a "now playing")        | Artist, title, album                                             |
| A track is finished (client sends a scrobble)        | Same as above, plus the time you played it, and the track length |

If the track has MusicBrainz IDs in your Beets library (recording, release, artist), they are sent too, so ListenBrainz can match the track exactly. **BeetstreamNext** also identifies itself as the submitting client.

> [!NOTE]
>
> Radio streams, podcasts, and tracks shorter than 30 seconds are not scrobbled.