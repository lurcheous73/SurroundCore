const crypto = require('crypto');
const { WebUSB } = require('usb');
const { DevicesIds, openNewDevice, listContent } = require('netmd-js');

function sha(parts) {
  return crypto.createHash('sha256').update(JSON.stringify(parts)).digest('hex');
}

function flattenTracks(disc) {
  const found = new Map();
  for (const group of (disc.groups || [])) {
    for (const t of (group.tracks || [])) {
      const index = Number(t.index ?? found.size);
      found.set(index, {
        index,
        title: t.title || '',
        full_width_title: t.fullWidthTitle || '',
        duration_units: Number(t.duration || 0),
        channel: Number(t.channel || 0),
        encoding: t.encoding,
        protected: t.protected,
        group_index: Number(group.index ?? -1),
        group_title: group.title || '',
      });
    }
  }
  return [...found.values()].sort((a,b) => a.index - b.index);
}

(async () => {
  let iface = null;
  try {
    const usb = new WebUSB({ allowedDevices: DevicesIds, deviceTimeout: 10000 });
    iface = await openNewDevice(usb);
    if (!iface) throw new Error('No NetMD device found');
    const netmd = iface.netMd;
    const device = netmd.getDeviceName() || 'NetMD';
    const vendor = typeof netmd.getVendor === 'function' ? netmd.getVendor() : null;
    const product = typeof netmd.getProduct === 'function' ? netmd.getProduct() : null;
    let level = 0; try { level = await iface.getNetMDLevel(); } catch (_) {}
    const hiMD = /MZ-(NH|RH|DH)|DS-HMD|CMT-AH/i.test(device);
    let disc = null;
    try { disc = await listContent(iface); }
    catch (e) { if (!hiMD) throw e; }

    if (!disc) {
      console.log(JSON.stringify({
        device, vendor, product, level, himd: true, rippable: false,
        disc_title: '', track_count: 0, used: 0, total: 0, tracks: [], groups: [],
        device_signature: sha([vendor, product, device]).slice(0,24),
        toc_hash: null,
        fingerprint: sha([vendor, product, device, 'Hi-MD', level]).slice(0,24)
      }));
      return;
    }

    const tracks = flattenTracks(disc);
    const tocBasis = tracks.map(t => [t.index, t.duration_units, t.encoding, t.channel]);
    const tocHash = sha(tocBasis);
    const groups = (disc.groups || []).map(g => ({
      index: Number(g.index ?? -1), title: g.title || '', full_width_title: g.fullWidthTitle || '',
      tracks: (g.tracks || []).map(t => Number(t.index ?? -1)).filter(x => x >= 0)
    }));
    const title = disc.title || '';
    console.log(JSON.stringify({
      device, vendor, product, level, himd: hiMD, rippable: true,
      writable: !!disc.writable, write_protected: !!disc.writeProtected,
      disc_title: title, full_width_title: disc.fullWidthTitle || '',
      track_count: Number(disc.trackCount || tracks.length), used: Number(disc.used || 0), total: Number(disc.total || 0),
      tracks, groups,
      device_signature: sha([vendor, product, device]).slice(0,24),
      toc_hash: tocHash,
      fingerprint: sha([vendor, product, tocHash]).slice(0,24)
    }));
  } finally {
    if (iface) { try { await iface.release(); } catch (_) {} }
    if (iface && iface.netMd) { try { await iface.netMd.finalize(); } catch (_) {} }
  }
})().then(() => process.exit(0)).catch(e => {
  console.error(e && (e.stack || e.message) || e);
  process.exit(1);
});
