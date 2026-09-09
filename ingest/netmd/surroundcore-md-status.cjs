const crypto = require('crypto');
const { WebUSB } = require('usb');
const { DevicesIds, openNewDevice, listContent } = require('netmd-js');
(async () => {
  let iface = null;
  try {
    const usb = new WebUSB({ allowedDevices: DevicesIds, deviceTimeout: 10000 });
    iface = await openNewDevice(usb);
    if (!iface) throw new Error('No NetMD device found');
    const disc = await listContent(iface);
    const device = iface.netMd.getDeviceName() || 'NetMD';
    const raw = [device, disc.title || '', disc.trackCount || 0, disc.used || 0, disc.total || 0].join('|');
    console.log(JSON.stringify({
      device, disc_title: disc.title || '', track_count: disc.trackCount || 0,
      used: disc.used || 0, total: disc.total || 0,
      fingerprint: crypto.createHash('sha256').update(raw).digest('hex').slice(0, 24)
    }));
  } finally {
    if (iface) { try { await iface.release(); } catch (_) {} }
    if (iface && iface.netMd) { try { await iface.netMd.finalize(); } catch (_) {} }
  }
})().then(() => process.exit(0)).catch(e => { console.error(e.message || e); process.exit(1); });
