# API coverage

> See [Features](./features/index.md) for what BeetstreamNext adds on top of the spec itself :)

BeetstreamNext implements essentially the entire Subsonic/OpenSubsonic REST API. The only unsupported endpoints are video-related:

- [`getCaptions`](https://opensubsonic.netlify.app/docs/endpoints/getcaptions/)
- [`getVideoInfo`](https://opensubsonic.netlify.app/docs/endpoints/getvideoinfo/)
- [`getVideos`](https://opensubsonic.netlify.app/docs/endpoints/getvideos/)

<details>
<summary>Full endpoint-by-endpoint checklist</summary>

- [x] [addChatMessage](https://opensubsonic.netlify.app/docs/endpoints/addchatmessage/)
- [x] [changePassword](https://opensubsonic.netlify.app/docs/endpoints/changepassword/)
- [x] [createBookmark](https://opensubsonic.netlify.app/docs/endpoints/createbookmark/)
- [x] [createInternetRadioStation](https://opensubsonic.netlify.app/docs/endpoints/createinternetradiostation/)
- [x] [createPlaylist](https://opensubsonic.netlify.app/docs/endpoints/createplaylist/)
- [x] [createPodcastChannel](https://opensubsonic.netlify.app/docs/endpoints/createpodcastchannel/)
- [x] [createShare](https://opensubsonic.netlify.app/docs/endpoints/createshare/)
- [x] [createUser](https://opensubsonic.netlify.app/docs/endpoints/createuser/)
- [x] [deleteBookmark](https://opensubsonic.netlify.app/docs/endpoints/deletebookmark/)
- [x] [deleteInternetRadioStation](https://opensubsonic.netlify.app/docs/endpoints/deleteinternetradiostation/)
- [x] [deletePlaylist](https://opensubsonic.netlify.app/docs/endpoints/deleteplaylist/)
- [x] [deletePodcastChannel](https://opensubsonic.netlify.app/docs/endpoints/deletepodcastchannel/)
- [x] [deletePodcastEpisode](https://opensubsonic.netlify.app/docs/endpoints/deletepodcastepisode/)
- [x] [deleteShare](https://opensubsonic.netlify.app/docs/endpoints/deleteshare/)
- [x] [deleteUser](https://opensubsonic.netlify.app/docs/endpoints/deleteuser/)
- [x] [download](https://opensubsonic.netlify.app/docs/endpoints/download/)
- [x] [downloadPodcastEpisode](https://opensubsonic.netlify.app/docs/endpoints/downloadpodcastepisode/)
- [x] [findSonicPath](https://opensubsonic.netlify.app/docs/endpoints/findsonicpath/)
- [x] [getAlbum](https://opensubsonic.netlify.app/docs/endpoints/getalbum/)
- [x] [getAlbumInfo](https://opensubsonic.netlify.app/docs/endpoints/getalbuminfo/)
- [x] [getAlbumInfo2](https://opensubsonic.netlify.app/docs/endpoints/getalbuminfo2/)
- [x] [getAlbumList](https://opensubsonic.netlify.app/docs/endpoints/getalbumlist/)
- [x] [getAlbumList2](https://opensubsonic.netlify.app/docs/endpoints/getalbumlist2/)
- [x] [getArtist](https://opensubsonic.netlify.app/docs/endpoints/getartist/)
- [x] [getArtistInfo](https://opensubsonic.netlify.app/docs/endpoints/getartistinfo/)
- [x] [getArtistInfo2](https://opensubsonic.netlify.app/docs/endpoints/getartistinfo2/)
- [x] [getArtists](https://opensubsonic.netlify.app/docs/endpoints/getartists/)
- [x] [getAvatar](https://opensubsonic.netlify.app/docs/endpoints/getavatar/)
- [x] [getBookmarks](https://opensubsonic.netlify.app/docs/endpoints/getbookmarks/)
- [ ] [getCaptions](https://opensubsonic.netlify.app/docs/endpoints/getcaptions/)
- [x] [getChatMessages](https://opensubsonic.netlify.app/docs/endpoints/getchatmessages/)
- [x] [getCoverArt](https://opensubsonic.netlify.app/docs/endpoints/getcoverart/)
- [x] [getGenres](https://opensubsonic.netlify.app/docs/endpoints/getgenres/)
- [x] [getIndexes](https://opensubsonic.netlify.app/docs/endpoints/getindexes/)
- [x] [getInternetRadioStations](https://opensubsonic.netlify.app/docs/endpoints/getinternetradiostations/)
- [x] [getLicense](https://opensubsonic.netlify.app/docs/endpoints/getlicense/)
- [x] [getLyrics](https://opensubsonic.netlify.app/docs/endpoints/getlyrics/)
- [x] [getLyricsBySongId](https://opensubsonic.netlify.app/docs/endpoints/getlyricsbysongid/)
- [x] [getMusicDirectory](https://opensubsonic.netlify.app/docs/endpoints/getmusicdirectory/)
- [x] [getMusicFolders](https://opensubsonic.netlify.app/docs/endpoints/getmusicfolders/)
- [x] [getNewestPodcasts](https://opensubsonic.netlify.app/docs/endpoints/getnewestpodcasts/)
- [x] [getNowPlaying](https://opensubsonic.netlify.app/docs/endpoints/getnowplaying/)
- [x] [getOpenSubsonicExtensions](https://opensubsonic.netlify.app/docs/endpoints/getopensubsonicextensions/)
- [x] [getPlaylist](https://opensubsonic.netlify.app/docs/endpoints/getplaylist/)
- [x] [getPlaylists](https://opensubsonic.netlify.app/docs/endpoints/getplaylists/)
- [x] [getPlayQueue](https://opensubsonic.netlify.app/docs/endpoints/getplayqueue/)
- [x] [getPlayQueueByIndex](https://opensubsonic.netlify.app/docs/endpoints/getplayqueuebyindex/)
- [x] [getPodcastEpisode](https://opensubsonic.netlify.app/docs/endpoints/getpodcastepisode/)
- [x] [getPodcasts](https://opensubsonic.netlify.app/docs/endpoints/getpodcasts/)
- [x] [getRandomSongs](https://opensubsonic.netlify.app/docs/endpoints/getrandomsongs/)
- [x] [getScanStatus](https://opensubsonic.netlify.app/docs/endpoints/getscanstatus/)
- [x] [getShares](https://opensubsonic.netlify.app/docs/endpoints/getshares/)
- [x] [getSimilarSongs](https://opensubsonic.netlify.app/docs/endpoints/getsimilarsongs/)
- [x] [getSimilarSongs2](https://opensubsonic.netlify.app/docs/endpoints/getsimilarsongs2/)
- [x] [getSong](https://opensubsonic.netlify.app/docs/endpoints/getsong/)
- [x] [getSongsByGenre](https://opensubsonic.netlify.app/docs/endpoints/getsongsbygenre/)
- [x] [getSonicSimilarTracks](https://opensubsonic.netlify.app/docs/endpoints/getsonicsimilartracks/)
- [x] [getStarred](https://opensubsonic.netlify.app/docs/endpoints/getstarred/)
- [x] [getStarred2](https://opensubsonic.netlify.app/docs/endpoints/getstarred2/)
- [x] [getTopSongs](https://opensubsonic.netlify.app/docs/endpoints/gettopsongs/)
- [x] [getTranscodeDecision](https://opensubsonic.netlify.app/docs/endpoints/gettranscodedecision/)
- [x] [getTranscodeStream](https://opensubsonic.netlify.app/docs/endpoints/gettranscodestream/)
- [x] [getUser](https://opensubsonic.netlify.app/docs/endpoints/getuser/)
- [x] [getUsers](https://opensubsonic.netlify.app/docs/endpoints/getusers/)
- [ ] [getVideoInfo](https://opensubsonic.netlify.app/docs/endpoints/getvideoinfo/)
- [ ] [getVideos](https://opensubsonic.netlify.app/docs/endpoints/getvideos/)
- [x] [hls](https://opensubsonic.netlify.app/docs/endpoints/hls/)
- [x] [jukeboxControl](https://opensubsonic.netlify.app/docs/endpoints/jukeboxcontrol/)
- [x] [ping](https://opensubsonic.netlify.app/docs/endpoints/ping/)
- [x] [refreshPodcasts](https://opensubsonic.netlify.app/docs/endpoints/refreshpodcasts/)
- [x] [reportPlayback](https://opensubsonic.netlify.app/docs/endpoints/reportplayback/)
- [x] [savePlayQueue](https://opensubsonic.netlify.app/docs/endpoints/saveplayqueue/)
- [x] [savePlayQueueByIndex](https://opensubsonic.netlify.app/docs/endpoints/saveplayqueuebyindex/)
- [x] [scrobble](https://opensubsonic.netlify.app/docs/endpoints/scrobble/)
- [x] [search](https://opensubsonic.netlify.app/docs/endpoints/search/)
- [x] [search2](https://opensubsonic.netlify.app/docs/endpoints/search2/)
- [x] [search3](https://opensubsonic.netlify.app/docs/endpoints/search3/)
- [x] [setRating](https://opensubsonic.netlify.app/docs/endpoints/setrating/)
- [x] [star](https://opensubsonic.netlify.app/docs/endpoints/star/)
- [x] [startScan](https://opensubsonic.netlify.app/docs/endpoints/startscan/)
- [x] [stream](https://opensubsonic.netlify.app/docs/endpoints/stream/)
- [x] [tokenInfo](https://opensubsonic.netlify.app/docs/endpoints/tokeninfo/)
- [x] [unstar](https://opensubsonic.netlify.app/docs/endpoints/unstar/)
- [x] [updateInternetRadioStation](https://opensubsonic.netlify.app/docs/endpoints/updateinternetradiostation/)
- [x] [updatePlaylist](https://opensubsonic.netlify.app/docs/endpoints/updateplaylist/)
- [x] [updateShare](https://opensubsonic.netlify.app/docs/endpoints/updateshare/)
- [x] [updateUser](https://opensubsonic.netlify.app/docs/endpoints/updateuser/)

</details>

> **Note:** Following the reference Subsonic specification, error responses are returned with an **HTTP 200** status. The actual outcome is in the response body's `status` field (`"ok"` or `"failed"`, with an error `code`/`message` on failure). Don't rely on the HTTP status alone to detect a failed call.

## Extensions

Alongside the base spec, BeetstreamNext implements every [OpenSubsonic extension](https://opensubsonic.netlify.app/docs/extensions/).

| Extension                                                                                    | Description                                                                                                  |
|----------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------|
| [`apiKeyAuthentication`](https://opensubsonic.netlify.app/docs/extensions/apikeyauth/)       | Authenticating with an API key alone, no password                                                            |
| [`formPost`](https://opensubsonic.netlify.app/docs/extensions/formpost/)                     | Accepting requests as `application/x-www-form-urlencoded` POST bodies                                        |
| [`getPodcastEpisode`](https://opensubsonic.netlify.app/docs/extensions/getpodcastepisode/)   | Retrieving a single podcast episode's metadata by ID                                                         |
| [`indexBasedQueue`](https://opensubsonic.netlify.app/docs/extensions/indexbasedqueue/)       | Setting/reading the play queue by index instead of only by ID                                                |
| [`playbackReport`](https://opensubsonic.netlify.app/docs/extensions/playbackreport/)         | Clients reporting their playback timeline back to the server                                                 |
| [`songLyrics`](https://opensubsonic.netlify.app/docs/extensions/songlyrics/)                 | Synchronized, multi-language lyrics, retrievable by song ID                                                  |
| [`topSongsByArtistId`](https://opensubsonic.netlify.app/docs/extensions/topsongsbyartistid/) | Retrieving an artist's top songs by artist ID                                                                |
| [`transcodeOffset`](https://opensubsonic.netlify.app/docs/extensions/transcodeoffset/)       | Starting a transcode from a given time offset                                                                |
| [`transcoding`](https://opensubsonic.netlify.app/docs/extensions/transcoding/)               | Clients making their own transcoding decisions and requesting transcoded streams directly                    |
| [`sonicSimilarity`](https://opensubsonic.netlify.app/docs/extensions/sonicsimilarity/)       | Acoustic similarity and playlist path-finding (see [Library augmentation](features/library-augmentation.md)) |

> **Note:** All these extensions are always advertised by the server except `sonicSimilarity`, which only appears if an [AudioMuse-AI instance is configured](./configuration.md#audiomuse_url).

## Authentication

Both authentication schemes from the spec are supported:

- **API-key authentication** (recommended): Always available.
- **Legacy MD5-token / cleartext password authentication**: For older clients. Can be enabled server-wide via [`legacy_auth`](./configuration.md#legacy_auth) if your client needs it.