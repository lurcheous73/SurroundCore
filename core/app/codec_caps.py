CODEC_NAMES = {
    'pcm_s16le':'PCM','pcm_s24le':'PCM','pcm_s32le':'PCM','pcm_f32le':'PCM',
    'flac':'FLAC','alac':'ALAC','wavpack':'WavPack','ape':'Monkey\'s Audio',
    'mp3':'MP3','aac':'AAC / HE-AAC','vorbis':'Ogg Vorbis','opus':'Opus',
    'ac3':'Dolby Digital (AC-3)','eac3':'Dolby Digital Plus (E-AC-3)',
    'dts':'DTS','dca':'DTS','truehd':'Dolby TrueHD','mlp':'MLP',
    'dsd_lsbf':'DSD','dsd_msbf':'DSD','dsd_lsbf_planar':'DSD','dsd_msbf_planar':'DSD',
}

PASSTHROUGH_ALIASES = {
    'ac3': ('AC-3','AC3','DOLBY DIGITAL'),
    'eac3': ('E-AC-3','EAC3','DOLBY DIGITAL +','DOLBY DIGITAL PLUS','DD+'),
    'dts': ('DTS','DTS-HD','DTS HD'),
    'truehd': ('TRUEHD','DOLBY TRUEHD','MLP'),
    'mlp': ('MLP','TRUEHD','DOLBY TRUEHD'),
}

def canonical(codec):
    c=str(codec or '').lower().strip()
    return 'dts' if c=='dca' else c

def display_name(codec):
    return CODEC_NAMES.get(canonical(codec), str(codec or 'Unknown'))

def _meta_text(media):
    meta=media.get('metadata') or {}
    return ' '.join(str(meta.get(k) or '') for k in ('codec_profile','codec_tag','comment','title')).upper()
def features(media):
    codec=canonical(media.get('codec'))
    text=_meta_text(media)
    profile=str((media.get('metadata') or {}).get('codec_profile') or '')
    atmos=('ATMOS' in text or 'JOC' in text) and codec in ('truehd','eac3')
    dtsx=('DTS:X' in text or 'DTS X' in text) and codec=='dts'
    dtshd=('DTS-HD' in text or 'DTS HD' in text or 'DTS-HD' in profile.upper()) and codec=='dts'
    mqa='MQA' in text
    dsd=codec.startswith('dsd') or str(media.get('path') or '').lower().endswith(('.dsf','.dff'))
    return {
        'codec':codec,'name':display_name(codec),'profile':profile,
        'atmos':atmos,'dtsx':dtsx,'dtshd':dtshd,'mqa':mqa,'dsd':dsd,
        'lossless':codec in ('flac','alac','wavpack','ape','truehd','mlp') or codec.startswith('pcm') or dsd,
        'encoded_passthrough':codec in PASSTHROUGH_ALIASES,
    }

def sink_encoded_formats(device):
    return {str(x).upper().replace('_',' ').strip() for x in (device or {}).get('encoded_formats',[]) if x}

def sink_supports_encoded(media, device):
    f=features(media); codec=f['codec']; sink=sink_encoded_formats(device)
    if codec not in PASSTHROUGH_ALIASES:return False
    return any(any(alias in item for alias in PASSTHROUGH_ALIASES[codec]) for item in sink)

def sink_supports_dsd(device):
    d=device or {}
    return bool(d.get('native_dsd') or d.get('dop_candidate'))
