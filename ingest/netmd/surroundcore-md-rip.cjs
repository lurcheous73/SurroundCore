const { WebUSB } = require('usb');
const { DevicesIds, openNewDevice, listContent } = require('netmd-js');
const {
  ExploitStateManager,
  AtracRecovery,
  CachedSectorControlDownload,
  getBestSuited,
  isCompatible,
} = require('netmd-exploits');
const fs = require('fs');
const path = require('path');

function flattenTrackCount(disc) {
  if (disc && Number.isInteger(disc.trackCount)) return disc.trackCount;
  let max = -1;
  for (const g of (disc?.groups || [])) for (const t of (g.tracks || [])) max = Math.max(max, Number(t.index ?? -1));
  return max + 1;
}

function shouldCancel(cancelPath) {
  return !!cancelPath && fs.existsSync(cancelPath);
}

(async () => {
  const mode = process.argv[2];
  const target = process.argv[3];
  const cancelPath = process.argv[4] || '';
  if (!mode || !target) throw new Error('usage: <track-index|all> <output-base|output-dir> [cancel-file]');

  let iface = null, state = null, exploit = null;
  try {
    const usb = new WebUSB({ allowedDevices: DevicesIds, deviceTimeout: 30000 });
    iface = await openNewDevice(usb);
    if (!iface) throw new Error('No NetMD device found');
    try { await iface.stop(); } catch (_) {}
    const factory = await iface.factory();
    state = await ExploitStateManager.create(iface, factory);

    let exploitCtor = getBestSuited(AtracRecovery, state.device);
    if (!exploitCtor && isCompatible(CachedSectorControlDownload, state.device)) exploitCtor = CachedSectorControlDownload;
    if (!exploitCtor) throw new Error(`No ATRAC recovery exploit supports ${state.device?.versionCode || 'this recorder'}`);
    exploit = await state.require(exploitCtor);
    console.log(`CMMDSTART\t${state.device?.versionCode || ''}\t${exploitCtor._name || exploitCtor.name || 'AtracRecovery'}`);

    let tracks = [];
    if (mode === 'all') {
      const disc = await listContent(iface);
      tracks = Array.from({length: flattenTrackCount(disc)}, (_,i) => i);
      fs.mkdirSync(target, {recursive:true});
    } else {
      const n = Number(mode);
      if (!Number.isInteger(n) || n < 0) throw new Error('track index must be >= 0');
      tracks = [n];
    }

    for (const track of tracks) {
      if (shouldCancel(cancelPath)) throw new Error('CMMD_CANCELLED');
      let lastPct = -1;
      console.log(`CMMDTRACK\t${track}`);
      const result = await exploit.downloadTrack(track, p => {
        if (shouldCancel(cancelPath)) throw new Error('CMMD_CANCELLED');
        const pct = p.total > 0 ? Math.floor((p.read / p.total) * 100) : 0;
        if (pct !== lastPct || p.action === 'SEEK') {
          lastPct = pct;
          console.log(`CMMDPROGRESS\t${track}\t${p.action}\t${p.read}\t${p.total}\t${pct}`);
        }
      });
      const ext = String(result.extension || 'aea').replace(/^\./, '') || 'aea';
      const out = mode === 'all' ? path.join(target, `${String(track+1).padStart(2,'0')}-track.${ext}`) : `${target}.${ext}`;
      fs.writeFileSync(out, Buffer.from(result.data));
      console.log(`CMMDDONE\t${track}\t${out}\t${result.data.length}\t${ext}`);
    }
  } finally {
    if (state && exploit) { try { await state.unload(exploit); } catch (e) { console.error(`CMMDUNLOAD\t${e.message}`); } }
    if (iface) { try { await iface.release(); } catch (_) {} }
    if (iface && iface.netMd) { try { await iface.netMd.finalize(); } catch (_) {} }
  }
})().then(() => process.exit(0)).catch(e => {
  console.error(`CMMDERROR\t${e && (e.stack || e.message) || e}`);
  process.exit(1);
});
