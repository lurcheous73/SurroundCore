def html():
    return r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SurroundCore Ingest</title><style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#111;color:#eee;margin:0}main{max-width:1050px;margin:auto;padding:28px}
.card{background:#1d1d1f;border-radius:14px;padding:18px;margin:14px 0}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
button,input{font:inherit;padding:9px 11px;border:1px solid #555;border-radius:9px;background:#29292b;color:#fff}button{cursor:pointer}
.ok{color:#73d17c}.warn{color:#f0c66b}.bad{color:#ff7777}.muted{color:#aaa}code{color:#9dd7ff}.job{border-top:1px solid #333;padding:10px 0}
</style></head><body><main><h1>SurroundCore Ingest</h1>
<p class="muted">Rip audio directly on the Core from SATA/USB optical drives, NetMD or uploaded ISO/BIN images. Video is never retained.</p>
<div class="card"><h2>Core access</h2><div class="row"><input id="token" type="password" placeholder="SurroundCore token"><button onclick="connect()">Connect</button></div><p id="auth" class="muted"></p></div>
<div id="app" hidden><div class="card"><h2>Capabilities</h2><div id="caps"></div></div>
<div class="card"><h2>Optical drives</h2><div id="drives"></div></div>
<div class="card"><h2>MiniDisc / NetMD</h2><div class="row"><button onclick="ripNetMD()">Rip inserted MiniDisc</button><span id="md"></span></div></div>
<div class="card"><h2>Upload ISO / BIN+CUE</h2><p class="muted">BIN and CUE can be selected together. Successful uploads are deleted after verified audio extraction unless configured otherwise.</p>
<input id="files" type="file" multiple accept=".iso,.bin,.cue"><button onclick="upload()">Upload & rip</button><span id="up"></span></div>
<div class="card"><h2>Jobs</h2><button onclick="loadJobs()">Refresh</button><div id="jobs"></div></div></div>
<script>
let TOKEN=sessionStorage.getItem('surroundcore-token')||'';const H=()=>({'Authorization':'Bearer '+TOKEN});
async function api(p,o={}){o.headers={...(o.headers||{}),...H()};let r=await fetch(p,o);if(!r.ok)throw new Error(await r.text());return await r.json()}
async function connect(){TOKEN=document.getElementById('token').value||TOKEN;if(!TOKEN)return;sessionStorage.setItem('surroundcore-token',TOKEN);try{let [c,d]=await Promise.all([api('/api/v1/ingest/capabilities'),api('/api/v1/ingest/devices')]);document.getElementById('app').hidden=false;document.getElementById('auth').innerHTML='<span class="ok">Connected</span>';renderCaps(c);renderDrives(d.optical||[]);renderMD(d.netmd);loadJobs()}catch(e){document.getElementById('auth').innerHTML='<span class="bad">'+e+'</span>'}}
function renderCaps(c){document.getElementById('caps').innerHTML=`<b>Auto-rip:</b> ${c.auto_rip?'on':'off'} · <b>MakeMKV:</b> ${c.makemkv?'available':'optional/not installed'} · <b>NetMD:</b> ${c.netmd_helper?'ready':'not installed'}<br><span class="muted">Audio CD · DVD-Video audio · DVD-A · Blu-ray audio · ISO · BIN/CUE · NetMD</span>`}
function renderDrives(ds){let b=document.getElementById('drives');b.innerHTML=ds.length?'':'<span class="muted">No SATA/USB optical drive detected.</span>';for(const d of ds){let x=document.createElement('div');x.className='row';x.style.margin='8px 0';x.innerHTML=`<b>${d.name}</b><code>${d.device}</code><span>${d.bus}</span><button>Rip now</button>`;x.querySelector('button').onclick=()=>ripDrive(d.device);b.appendChild(x)}}
function renderMD(m){document.getElementById('md').textContent=m&&m.available?`${m.device} · ${m.disc_title||'disc'} · ${m.track_count} tracks`:(m&&m.reason)||'No NetMD detected'}
async function ripDrive(device){await api('/api/v1/ingest/rip',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device,force:true})});loadJobs()}
async function ripNetMD(){await api('/api/v1/ingest/netmd',{method:'POST'});loadJobs()}
async function upload(){let fs=document.getElementById('files').files;if(!fs.length)return;let fd=new FormData();for(const f of fs)fd.append('files',f);document.getElementById('up').textContent='Uploading…';let r=await fetch('/api/v1/ingest/upload',{method:'POST',headers:H(),body:fd});document.getElementById('up').textContent=r.ok?'Queued':'Upload failed';if(!r.ok)alert(await r.text());loadJobs()}
async function loadJobs(){let d=await api('/api/v1/ingest/jobs');let b=document.getElementById('jobs');b.innerHTML='';for(const j of d.jobs){let cls=j.status==='done'?'ok':j.status==='failed'?'bad':'warn';let x=document.createElement('div');x.className='job';x.innerHTML=`<b>${j.label||j.source}</b> · <span class="${cls}">${j.status}</span><br><code>${j.kind} · ${j.source}</code>${j.error?'<br><span class="bad">'+j.error+'</span>':''}${j.result?'<br><span class="muted">'+((j.result.outputs||[]).length)+' verified audio output(s)</span>':''}`;b.appendChild(x)}}
if(TOKEN){document.getElementById('token').value=TOKEN;connect()}setInterval(()=>{if(!document.getElementById('app').hidden)loadJobs()},5000);
</script></main></body></html>'''
