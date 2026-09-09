def streaming_setup_html():
    parts = [r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SurroundCore Streaming</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:#111;color:#eee}
main{max-width:980px;margin:auto;padding:28px}.card{background:#1d1d1f;border-radius:14px;padding:18px;margin:14px 0}
h1,h2{margin:.2em 0}.muted{color:#aaa}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}
button,input,select{font:inherit;padding:9px 11px;border-radius:9px;border:1px solid #555;background:#29292b;color:#fff}
button{cursor:pointer}.ok{color:#73d17c}.warn{color:#f0c66b}.bad{color:#ff7777}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.provider{border:1px solid #38383b;border-radius:12px;padding:14px}.provider h3{margin:0 0 7px}.pill{font-size:.8em;padding:3px 7px;border-radius:99px;background:#333}
label{display:flex;justify-content:space-between;gap:15px;margin:9px 0}code{color:#9dd7ff}
</style></head><body><main>
<h1>SurroundCore Streaming</h1>
<p class="muted">Accounts, source quality and native streaming live on the Core. ControlMac remains a controller.</p>
<div class="card" id="login"><h2>Core access</h2><div class="row"><input id="token" type="password" placeholder="SurroundCore token"><button onclick="connect()">Connect</button></div><p id="authstate" class="muted">Enter the Core token once for this browser session.</p></div>
<div id="app" hidden>
<div class="card"><h2>Quality policy</h2><p class="muted">Highest native resolution first. No downsample or downmix unless explicitly enabled.</p>
<div id="quality"></div></div>
<div class="card"><h2>Streaming services</h2><div id="providers" class="grid"></div></div>
''']
    parts.append(r'''
<div class="card"><h2>Bandcamp</h2>
<p class="muted">Use the Subsonic credentials generated in Bandcamp Fan Settings. Credentials stay on SurroundCore.</p>
<div class="row"><input id="bcServer" size="34" value="https://bandcamp.com/api/subsonic"><input id="bcUser" placeholder="Bandcamp Subsonic username"><input id="bcPass" type="password" placeholder="Bandcamp Subsonic password"><button onclick="saveBandcamp()">Test & Save</button><button onclick="disconnectBandcamp()">Disconnect</button><button onclick="loadBandcampAlbums()">Browse purchases</button></div>
<div id="bandcampState" class="muted"></div><div id="bandcampLibrary"></div></div>
<div class="card"><h2>Podcasts / RSS</h2>
<div class="row"><input id="podName" placeholder="Optional name"><input id="podUrl" size="44" placeholder="https://example.com/feed.xml"><button onclick="addPodcast()">Add feed</button></div>
<div id="podcasts"></div></div>
<div class="card"><h2>Internet Radio</h2>
<div class="row"><input id="stationName" placeholder="Station name"><input id="stationUrl" size="42" placeholder="https://stream.example/radio"><button onclick="addStation()">Add station</button></div>
<div id="stations"></div></div>
</div>
<script>
let TOKEN=sessionStorage.getItem('surroundcore-token')||'';let ENDPOINTS=[];
const auth=()=>({'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const endpointOptions=()=>ENDPOINTS.map(e=>`<option value="${esc(e.id)}">${esc(e.name)}</option>`).join('');
function hashToken(){if(location.hash.startsWith('#token=')){TOKEN=decodeURIComponent(location.hash.slice(7));sessionStorage.setItem('surroundcore-token',TOKEN);history.replaceState(null,'',location.pathname+location.search)}}
async function api(path,opt={}){opt.headers={...(opt.headers||{}),...auth()};let r=await fetch(path,opt);if(!r.ok)throw new Error(await r.text());return await r.json()}
async function connect(){TOKEN=document.getElementById('token').value||TOKEN;if(!TOKEN)return;sessionStorage.setItem('surroundcore-token',TOKEN);try{await load();document.getElementById('app').hidden=false;document.getElementById('authstate').textContent='Connected';document.getElementById('authstate').className='ok'}catch(e){document.getElementById('authstate').textContent='Connection failed';document.getElementById('authstate').className='bad'}}
async function load(){let [d,e]=await Promise.all([api('/api/v1/streaming'),api('/api/v1/endpoints')]);ENDPOINTS=(e.endpoints||[]).filter(x=>x.address&&(x.kind==='alsa'||x.kind==='meridian'));renderQuality(d.quality);renderProviders(d.providers);renderStations(d.settings.radio_stations||[]);renderPodcastFeeds(d.settings.podcast_feeds||[]);loadBandcampStatus()}
function renderQuality(q){let a=q.airia_available?'available':'licensed module not installed';document.getElementById('quality').innerHTML=`<p><b>Source:</b> highest native · <b>AIRIA:</b> ${a} · <b>MQA:</b> pass-through ${q.mqa_passthrough?'on':'off'} · <b>Output:</b> ${q.output_transport}</p><p class="muted">MHR/MMHR are only selected when a Meridian endpoint genuinely advertises the transport.</p>`}
''')
    parts.append(r'''
function renderProviders(items){let box=document.getElementById('providers');box.innerHTML='';for(const p of items){let state=p.implemented?'<span class="ok">Core module ready</span>':'<span class="warn">Official integration/partner module pending</span>';let conn=p.connected?'<br><span class="ok">Account connected</span>':'';let air=p.airia_available===undefined?'':`<br>AIRIA: ${p.airia_available?'available':'licensed module required'}`;let solo=p.soloist_available===undefined?'':`<br>Spotify Soloist: ${p.soloist_available?'installed':'install from Spotify Developer'}`;box.insertAdjacentHTML('beforeend',`<div class="provider"><h3>${p.name}</h3><span class="pill">${p.kind}</span><p>${state}${conn}</p><p class="muted">Auth: ${p.auth}<br>Quality: ${p.quality}<br>${(p.formats||[]).join(' · ')}${air}${solo}</p></div>`)}}

async function loadBandcampStatus(){try{let d=await api('/api/v1/providers/bandcamp');document.getElementById('bandcampState').textContent=d.configured?`Connected as ${d.username}`:'Not connected';if(d.server)document.getElementById('bcServer').value=d.server;if(d.username)document.getElementById('bcUser').value=d.username}catch(e){document.getElementById('bandcampState').textContent='Bandcamp status unavailable'}}
async function saveBandcamp(){let server=document.getElementById('bcServer').value.trim(),username=document.getElementById('bcUser').value.trim(),password=document.getElementById('bcPass').value;if(!username||!password)return;let b=document.getElementById('bandcampState');b.textContent='Testing Bandcamp…';try{let d=await api('/api/v1/providers/bandcamp/configure',{method:'POST',body:JSON.stringify({server,username,password})});b.textContent=`Connected as ${d.account.username}`;document.getElementById('bcPass').value='';await load()}catch(e){b.textContent='Bandcamp login failed: '+e.message}}
async function disconnectBandcamp(){await api('/api/v1/providers/bandcamp/configure',{method:'DELETE'});document.getElementById('bandcampState').textContent='Disconnected';document.getElementById('bandcampLibrary').innerHTML='';await load()}
async function loadBandcampAlbums(){let box=document.getElementById('bandcampLibrary');box.textContent='Loading purchases…';try{let d=await api('/api/v1/providers/bandcamp/albums?size=200');box.innerHTML='';for(const a of d.albums||[]){let row=document.createElement('div');row.className='row';row.style.marginTop='8px';let id=encodeURIComponent(a.id||'');row.innerHTML=`<b>${esc(a.artist||'')}</b><span>${esc(a.name||a.title||'Album')}</span><button>Open</button>`;row.querySelector('button').onclick=()=>openBandcampAlbum(id);box.appendChild(row)}}catch(e){box.textContent='Bandcamp browse failed: '+e.message}}
async function openBandcampAlbum(encodedId){let box=document.getElementById('bandcampLibrary');let d=await api('/api/v1/providers/bandcamp/albums/'+encodedId),a=d.album||{};box.innerHTML=`<h3>${esc(a.artist||'')} — ${esc(a.name||a.title||'Album')}</h3>`;let i=0;for(const t of a.song||[]){let n=i++,row=document.createElement('div');row.className='row';row.style.marginTop='7px';row.innerHTML=`<span>${esc(t.track||'')} ${esc(t.title||'Track')}</span><span class="muted">${esc(t.suffix||t.contentType||'')}</span><select>${endpointOptions()}</select><button>Play</button>`;let sel=row.querySelector('select');row.querySelector('button').onclick=()=>playBandcamp(t.id,sel);box.appendChild(row)}}
async function playBandcamp(songId,sel){if(!sel.value){alert('No playable endpoint is registered');return}await api('/api/v1/streaming/play',{method:'POST',body:JSON.stringify({provider:'bandcamp',item_id:String(songId),endpoint_id:sel.value})})}
function renderPodcastFeeds(items){let box=document.getElementById('podcasts');box.innerHTML='';for(const f of items){let row=document.createElement('div');row.style.marginTop='10px';row.innerHTML=`<div class="row"><b>${esc(f.name)}</b><code>${esc(f.url)}</code><button class="episodes">Episodes</button><button class="remove">Remove</button></div><div id="pod-eps-${esc(f.id)}"></div>`;row.querySelector('.episodes').onclick=()=>loadPodcastEpisodes(f.id);row.querySelector('.remove').onclick=()=>removePodcast(f.id);box.appendChild(row)}}
async function addPodcast(){let name=document.getElementById('podName').value.trim(),url=document.getElementById('podUrl').value.trim();if(!url)return;let d=await api('/api/v1/providers/podcasts',{method:'POST',body:JSON.stringify({name:name||null,url})});renderPodcastFeeds(d.podcast_feeds);document.getElementById('podName').value='';document.getElementById('podUrl').value=''}
async function removePodcast(id){let d=await api('/api/v1/providers/podcasts/'+encodeURIComponent(id),{method:'DELETE'});renderPodcastFeeds(d.podcast_feeds)}
async function loadPodcastEpisodes(id){let box=document.getElementById('pod-eps-'+id);box.textContent='Loading episodes…';try{let d=await api('/api/v1/providers/podcasts/'+encodeURIComponent(id)+'/episodes');box.innerHTML='';for(const ep of d.episodes||[]){let row=document.createElement('div');row.className='row';row.style.marginTop='6px';row.innerHTML=`<span>${esc(ep.title)}</span><span class="muted">${esc(ep.published||'')}</span><select>${endpointOptions()}</select><button>Play</button>`;let sel=row.querySelector('select');row.querySelector('button').onclick=()=>playPodcast(id,ep.id,sel);box.appendChild(row)}}catch(e){box.textContent='Podcast load failed: '+e.message}}
async function playPodcast(feedId,episodeId,sel){if(!sel.value){alert('No playable endpoint is registered');return}await api('/api/v1/streaming/play',{method:'POST',body:JSON.stringify({provider:'podcasts',item_id:feedId+':'+episodeId,endpoint_id:sel.value})})}

function renderStations(items){let box=document.getElementById('stations');box.innerHTML='';let opts=endpointOptions();for(const s of items){let d=document.createElement('div');d.className='row';d.style.marginTop='10px';d.innerHTML=`<b>${s.name}</b><code>${s.url}</code><select id="ep-${s.id}">${opts}</select><button class="play">Play</button><button class="stop">Stop</button><button class="remove">Remove</button>`;d.querySelector('.play').onclick=()=>playStation(s.id);d.querySelector('.stop').onclick=()=>stopStation(s.id);d.querySelector('.remove').onclick=()=>removeStation(s.id);box.appendChild(d)}}
async function addStation(){let name=document.getElementById('stationName').value.trim(),url=document.getElementById('stationUrl').value.trim();if(!name||!url)return;let d=await api('/api/v1/streaming/radio',{method:'POST',body:JSON.stringify({name,url})});renderStations(d.radio_stations);document.getElementById('stationName').value='';document.getElementById('stationUrl').value=''}
async function playStation(id){let ep=document.getElementById('ep-'+id);if(!ep||!ep.value){alert('No playable SurroundCore endpoint is registered');return}await api('/api/v1/streaming/play',{method:'POST',body:JSON.stringify({provider:'internet_radio',item_id:id,endpoint_id:ep.value})})}
async function stopStation(id){let ep=document.getElementById('ep-'+id);if(!ep||!ep.value)return;await api('/api/v1/playback/'+encodeURIComponent(ep.value)+'/stop',{method:'POST'})}
async function removeStation(id){let d=await api('/api/v1/streaming/radio/'+encodeURIComponent(id),{method:'DELETE'});renderStations(d.radio_stations)}
hashToken();if(TOKEN){document.getElementById('token').value=TOKEN;connect()}
</script></main></body></html>''')
    return ''.join(parts)
