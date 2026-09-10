from pathlib import Path


def streaming_setup_html():
    return (Path(__file__).with_name('webui.html')).read_text(encoding='utf-8')
