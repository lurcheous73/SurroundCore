import hashlib
import xml.etree.ElementTree as ET

import httpx


def _text(node, name):
    child = node.find(name)
    return (child.text or '').strip() if child is not None and child.text else ''


def _episode_id(url):
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def fetch_feed(url, timeout=20.0):
    response = httpx.get(url, timeout=timeout, follow_redirects=True,
                         headers={'User-Agent': 'SurroundCore/0.4'})
    response.raise_for_status()
    root = ET.fromstring(response.content)
    channel = root.find('channel')
    if channel is None:
        channel = root
    title = _text(channel, 'title') or url
    episodes = []
    for item in channel.findall('item'):
        enclosure = item.find('enclosure')
        media_url = enclosure.get('url') if enclosure is not None else None
        media_type = enclosure.get('type') if enclosure is not None else None
        if not media_url:
            continue
        episodes.append({
            'id': _episode_id(media_url),
            'title': _text(item, 'title') or media_url,
            'url': media_url,
            'media_type': media_type,
            'published': _text(item, 'pubDate'),
            'guid': _text(item, 'guid'),
        })
    if not episodes:
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        for entry in root.findall('.//atom:entry', ns):
            link = next((x for x in entry.findall('atom:link', ns)
                         if x.get('rel') == 'enclosure' and x.get('href')), None)
            if link is None:
                continue
            media_url = link.get('href')
            episodes.append({
                'id': _episode_id(media_url),
                'title': _text(entry, '{http://www.w3.org/2005/Atom}title') or media_url,
                'url': media_url,
                'media_type': link.get('type'),
                'published': _text(entry, '{http://www.w3.org/2005/Atom}updated'),
                'guid': _text(entry, '{http://www.w3.org/2005/Atom}id'),
            })
    return {'title': title, 'url': url, 'episodes': episodes}
