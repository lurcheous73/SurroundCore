const { WebUSB } = require('usb');
const { DevicesIds, openNewDevice } = require('netmd-js');
const { ExploitStateManager, CachedSectorControlDownload } = require('netmd-exploits');
const fs = require('fs');

(async () => {
  const track = Number(process.argv[2]);
  const outBase = process.argv[3];
  const cancelPath = process.argv[4] || '';
  if (!Number.isInteger(track) || track < 0 || !outBase) throw new Error('usage: track-index output-base [cancel-file]');
  let state, loaded = false, lastPct = -1;
  try {
    const usb = new WebUSB({ allowedDevices: DevicesIds, deviceTimeout: 30000 });
    const iface = await openNewDevice(usb);
    if (!iface) throw new Error('No NetMD device found');
    const factory = await iface.factory();
    state = await ExploitStateManager.create(iface, factory);
    const exploit = await state.require(CachedSectorControlDownload); loaded = true;
    console.log(`CMMDSTART\t${state.device.versionCode}\t${track}`);
    const result = await exploit.downloadTrack(track, p => {
      if (cancelPath && fs.existsSync(cancelPath)) throw new Error('CMMD_CANCELLED');
      const pct = p.total > 0 ? Math.floor((p.read / p.total) * 100) : 0;
      if (pct !== lastPct || p.action === 'SEEK') { lastPct = pct; console.log(`CMMDPROGRESS\t${p.action}\t${p.read}\t${p.total}\t${pct}`); }
    });
    const ext = String(result.extension || 'aea').replace(/^\./, '');
    const out = `${outBase}.${ext}`; fs.writeFileSync(out, Buffer.from(result.data));
    console.log(`CMMDDONE\t${out}\t${result.data.length}`);
  } finally {
    if (state && loaded) { try { await state.unload(CachedSectorControlDownload); } catch (e) { console.error(`CMMDUNLOAD\t${e.message}`); } }
  }
})().then(() => process.exit(0)).catch(e => { console.error(`CMMDERROR\t${e.stack || e}`); process.exit(1); });