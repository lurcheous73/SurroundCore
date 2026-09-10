from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class Protocol:
    id: str
    name: str
    rank: int
    mode: str
    family: str
    controls: tuple[str, ...]
    notes: str = ''

PROTOCOLS = {
    'sonos': Protocol('sonos','Sonos',10,'native','network',('play','pause','resume','stop','seek','volume','mute','status')),
    'airplay': Protocol('airplay','AirPlay',20,'native','network',('play','pause','resume','stop','seek','status')),
    'cast': Protocol('cast','Chromecast / Google Cast',30,'native','network',('play','pause','resume','stop','seek','volume','mute','status')),
    'upnp': Protocol('upnp','UPnP / DLNA / OpenHome',40,'native','network',('play','pause','resume','stop','seek','volume','mute','status')),
    'squeezebox': Protocol('squeezebox','Squeezebox / LMS',50,'bridge','network',('play','pause','resume','stop','seek','volume','mute','status')),
    'meridian': Protocol('meridian','Meridian / Sooloos',60,'native','network',('wake','play','pause','resume','stop','seek','volume','mute','status'),'SpeakerLink wake uses source 2'),
}
PROTOCOLS.update({
    'hqplayer': Protocol('hqplayer','HQPlayer / NAA',70,'bridge','network',('play','pause','resume','stop','seek','volume','status')),
    'devialet_air': Protocol('devialet_air','Devialet AIR',80,'bridge','network',('play','pause','resume','stop','volume','mute','status')),
    'kef': Protocol('kef','KEF Network Audio',90,'bridge','network',('play','pause','resume','stop','volume','mute','status')),
    'snapcast': Protocol('snapcast','Snapcast',100,'bridge','network',('play','pause','resume','stop','volume','mute','status')),
    'bluetooth': Protocol('bluetooth','Bluetooth / BlueZ',110,'local','local',('play','pause','resume','stop','volume','mute','status')),
    'alsa': Protocol('alsa','Local PCM / USB / HDMI',120,'local','local',('play','pause','resume','stop','seek','volume','mute','status')),
})

def protocol(kind):
    return PROTOCOLS.get(kind)

def rank(kind):
    item=protocol(kind)
    return item.rank if item else 999

def describe(kind):
    item=protocol(kind)
    return asdict(item) if item else {'id':kind,'name':kind,'rank':999,'mode':'unknown','family':'unknown','controls':[]}

def catalog():
    return [asdict(x) for x in sorted(PROTOCOLS.values(), key=lambda x:x.rank)]