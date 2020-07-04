# coding=utf-8
from __future__ import absolute_import
from datetime import datetime, timedelta
import time
import logging
try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO
import re
import os
try:
    from urllib.request import urlopen
except ImportError:
    from urllib2 import urlopen
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse
from contextlib import closing
import requests
import backoff


POST_PIC_INTERVAL_SECONDS = 10.0
if os.environ.get('DEBUG'):
    POST_PIC_INTERVAL_SECONDS = 3.0

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

def webcam_full_url(url):
    if not url or not url.strip():
        return None

    full_url = url.strip()
    if not urlparse(full_url).scheme:
        full_url = "http://localhost/" + re.sub(r"^\/", "", full_url)

    return full_url


@backoff.on_exception(backoff.expo, Exception, max_tries=6)
@backoff.on_predicate(backoff.expo, max_tries=6)
def capture_jpeg(webcam_settings):
    snapshot_url = webcam_full_url(webcam_settings.get("snapshot", ''))
    if snapshot_url:
        snapshot_validate_ssl = bool(webcam_settings.get("snapshotSslValidation", 'False'))

        r = requests.get(snapshot_url, stream=True, timeout=5, verify=snapshot_validate_ssl )
        r.raise_for_status()
        jpg = r.content
        return jpg

    else:
        stream_url = webcam_full_url(webcam_settings.get("stream", "/webcam/?action=stream"))

        with closing(urlopen(stream_url)) as res:
            chunker = MjpegStreamChunker()

            while True:
                data = res.readline()
                mjpg = chunker.findMjpegChunk(data)
                if mjpg:
                    res.close()
                    mjpeg_headers_index = mjpg.find('\r\n'*2)
                    if mjpeg_headers_index > 0:
                        return mjpg[mjpeg_headers_index+4:]
                    else:
                        raise Exception('Wrong mjpeg data format')


class MjpegStreamChunker:

    def __init__(self):
        self.boundary = None
        self.current_chunk = StringIO()

    # Return: mjpeg chunk if found
    #         None: in the middle of the chunk
    def findMjpegChunk(self, line):
        if not self.boundary:   # The first time endOfChunk should be called with 'boundary' text as input
            self.boundary = line
            self.current_chunk.write(line)
            return None

        if len(line) == len(self.boundary) and line == self.boundary:  # start of next chunk
            return self.current_chunk.getvalue()

        self.current_chunk.write(line)
        return None


class JpegPoster:

    def __init__(self, plugin):
        self.plugin = plugin
        self.last_jpg_post_ts = 0

    def post_jpeg_if_needed(self, force=False):
        if not self.plugin.is_configured():
            return

        if not self.plugin._printer.get_state_id() in ['PRINTING',]:
            return

        if not force:
            interval_seconds = POST_PIC_INTERVAL_SECONDS
            if not self.plugin.remote_status['viewing'] and not self.plugin.remote_status['should_watch']:
                interval_seconds *= 12      # Slow down jpeg posting if needed

            if self.last_jpg_post_ts > time.time() - interval_seconds:
                return

        self.last_jpg_post_ts = time.time()

        endpoint = self.plugin.canonical_endpoint_prefix() + '/api/v1/octo/pic/'
        try:
            self.plugin.error_stats.attempt('webcam')
            files = {'pic': capture_jpeg(self.plugin._settings.global_get(["webcam"]))}
        except:
            self.plugin.error_stats.add_connection_error('webcam')
            return

        resp = requests.post( endpoint, files=files, headers=self.plugin.auth_headers(), timeout=60)
        resp.raise_for_status()
        _logger.debug('Jpeg posted to server')

