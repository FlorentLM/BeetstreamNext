# OpenSubsonic API

## Recent additions
- `getUsers` / `getUser`
- `star` / `unstar` / `getStarred`
- `setRating`
- `getBookmarks` / `createBookmark` / `deleteBookmark`
- `getPlayQueue` / `savePlayQueue`
- `getLyrics` / `getLyricsBySongId`
- `updatePlaylist`
- `scrobble` (local play count tracking)
- `getSimilarSongs` / `getTopSongs` (Last.fm integration)

## Missing

### User management via API
- `createUser` (currently CLI only via `--user`)
- `updateUser`
- `deleteUser`
- `changePassword`
- `getAvatar`

### Social stuff
- `getShares` / `createShare` / `updateShare` / `deleteShare`
- `getNowPlaying` (aggregate currently playing from all users)
- `getChatMessages` / `addChatMessage`

### Stuff that could be fun but needs to bridge other plugins or other softwares
- `getPodcasts`
- `getInternetRadioStations`

### Low priority or out of scope
- `jukeboxControl`
- `getVideos`