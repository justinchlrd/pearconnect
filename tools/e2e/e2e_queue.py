from e2e import *
up = ['fJ9rUzIMcZQ', 'kJQP7kiw5Fk', 'yBwD4iYcWC4', 'JGwWNGJdvx8', 'OPf0YbXqDm0']
st = PlaybackState('dQw4w9WgXcQ', 30, False, duration=213, title='Never Gonna', artist='Rick Astley', playlist_id='RDAMVMdQw4w9WgXcQ', upcoming=up)
phone.put_state('telephone', st, kind='android'); time.sleep(1)
web.transfer('pc-test')
time.sleep(9)
print(player())
q = ev('(() => { const r = document.querySelector("#queue").queue.getItems().map(i => (i.playlistPanelVideoRenderer || i.playlistPanelVideoWrapperRenderer?.primaryRenderer?.playlistPanelVideoRenderer)); const k = r.findIndex(x => x && x.selected); return r.slice(k+1).map(x => x && x.videoId); })()')
print('upcoming on target:', q[:6], 'total', len(q))
print('identical:', q[:5] == up)
