from e2e import *
st = PlaybackState('dQw4w9WgXcQ', 90, False, duration=213, title='Never Gonna Give You Up', artist='Rick Astley', playlist_id='RDAMVMdQw4w9WgXcQ', volume=60)
phone.put_state('telephone', st, kind='android'); time.sleep(2)
web.transfer('pc-test')
t0=time.time()
for i in range(25):
    time.sleep(1)
    p = player()
    if p['id']=='dQw4w9WgXcQ' and p['st']==1 and 'list=' in p['url']: break
print('mix transfer', round(time.time()-t0,1),'s', p)
time.sleep(4)
s = web.snapshot()['devices']['pc-test']['state']; print({k:s[k] for k in ('video_id','position','paused','playlist_id','volume','upcoming')})
q = s['queue']; target = q[3]['video_id']; web.control('jump', video_id=target); time.sleep(4); print('jump', target, '->', player()['id'])
web.control('next'); time.sleep(4); print('next ->', player()['id'], 'expected', q[4]['video_id'])
web.control('previous'); time.sleep(4); print('previous ->', player()['id'])
web.control('enqueue', video_id='kJQP7kiw5Fk', next=True); time.sleep(3)
print('enqueue ->', ev('(() => { const items = document.querySelector("#queue").queue.getItems(); const r = items.map(i => (i.playlistPanelVideoRenderer || i.playlistPanelVideoWrapperRenderer?.primaryRenderer?.playlistPanelVideoRenderer)); const k = r.findIndex(x => x && x.selected); return r[k+1] && r[k+1].videoId; })()'))
# PC -> telephone
phone.put_state('telephone', None, kind='android'); time.sleep(1)
web.transfer('telephone'); time.sleep(2)
cmds = phone.take_commands('telephone'); print('phone got', [(c['type'], c.get('state', {}).get('playlist_id'), round(c.get('state', {}).get('position', 0))) for c in cmds])
time.sleep(2); print('pc after transfer ->', player()['st'])
shot('mix')
