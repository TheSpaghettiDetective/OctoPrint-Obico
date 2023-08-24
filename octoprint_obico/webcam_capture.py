# coding=utf-8
from __future__ import absolute_import
import base64
from datetime import datetime, timedelta
import time
import logging
import threading

try:
    from StringIO import StringIO as Buffer
    MJPEG_HDR = '\r\n' * 2
except ImportError:
    from io import BytesIO as Buffer
    MJPEG_HDR = b'\r\n' * 2

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

from .lib.error_stats import error_stats
from .utils import server_request, octoprint_webcam_settings


POST_PIC_INTERVAL_SECONDS = 10.0
if os.environ.get('DEBUG'):
    POST_PIC_INTERVAL_SECONDS = 3.0

_logger = logging.getLogger('octoprint.plugins.obico')

def webcam_full_url(url):
    if not url or not url.strip():
        return None

    full_url = url.strip()
    if not urlparse(full_url).scheme:
        full_url = "http://localhost/" + re.sub(r"^\/", "", full_url)

    return full_url


@backoff.on_exception(backoff.expo, Exception, max_tries=3)
@backoff.on_predicate(backoff.expo, max_tries=3)
def capture_jpeg(plugin, force_stream_url=False, use_nozzle_config=False):
    MAX_JPEG_SIZE = 5000000

    if use_nozzle_config:
        webcam_settings = plugin
    else:
        webcam_settings = octoprint_webcam_settings(plugin._settings)
    
    snapshot_url = webcam_full_url(webcam_settings.get("snapshot", ''))
    if snapshot_url and not force_stream_url:
        snapshot_validate_ssl = bool(webcam_settings.get("snapshotSslValidation", 'False'))

        r = requests.get(snapshot_url, stream=True, timeout=5, verify=snapshot_validate_ssl)
        r.raise_for_status()

        response_content = b''
        start_time = time.monotonic()
        for chunk in r.iter_content(chunk_size=1024):
            response_content += chunk
            if len(response_content) > MAX_JPEG_SIZE:
                r.close()
                raise Exception('Payload returned from the snapshot_url is too large. Did you configure stream_url as snapshot_url?')

        r.close()
        return response_content

    else:
        stream_url = webcam_full_url(webcam_settings.get("stream", "/webcam/?action=stream"))
        if not stream_url:
            raise Exception('Invalid Webcam snapshot URL "{}" or stream URL: "{}"'.format(snapshot_url, stream_url))

        with closing(urlopen(stream_url)) as res:
            chunker = MjpegStreamChunker()

            data_bytes = 0
            while True:
                data = res.readline()
                data_bytes += len(data)
                if data == b'':
                    raise Exception('End of stream before a valid jpeg is found')
                if data_bytes > MAX_JPEG_SIZE:
                    raise Exception('Reached the size cap before a valid jpeg is found.')

                mjpg = chunker.findMjpegChunk(data)
                if mjpg:
                    res.close()

                    mjpeg_headers_index = mjpg.find(MJPEG_HDR)
                    if mjpeg_headers_index > 0:
                        return mjpg[mjpeg_headers_index+4:]
                    else:
                        raise Exception('Wrong mjpeg data format')


class MjpegStreamChunker:

    def __init__(self):
        self.boundary = None
        self.current_chunk = Buffer()

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
        self.need_viewing_boost = threading.Event()

    def post_pic_to_server(self, viewing_boost=False):
        try:
            error_stats.attempt('webcam')
            files = {'pic': capture_jpeg(self.plugin)}
        except Exception as e:
            error_stats.add_connection_error('webcam', self.plugin)
            _logger.warning('Failed to capture jpeg - ' + str(e))
            return

        data = {'viewing_boost': 'true'} if viewing_boost else {}
        resp = server_request('POST', '/api/v1/octo/pic/', self.plugin, timeout=60, files=files, data=data, skip_debug_logging=True, headers=self.plugin.auth_headers())
        _logger.debug('Jpeg posted to server - {0}'.format(resp))

    def pic_post_loop(self):
        while True:
            try:
                viewing_boost = self.need_viewing_boost.wait(1)

                if not self.plugin.is_configured():
                    continue

                if viewing_boost:
                    self.need_viewing_boost.clear()
                    repeats = 3 if self.plugin.is_pro_user() else 1 # Pro users get better viewing boost
                    for _ in range(repeats):
                        self.post_pic_to_server(viewing_boost=True)
                    continue

                if not self.plugin._printer.get_state_id() in ['PRINTING',]:
                    continue

                interval_seconds = POST_PIC_INTERVAL_SECONDS
                if not self.plugin.remote_status['viewing'] and not self.plugin.remote_status['should_watch']:
                    interval_seconds *= 12      # Slow down jpeg posting if needed

                if self.last_jpg_post_ts > time.time() - interval_seconds:
                    continue

                self.last_jpg_post_ts = time.time()
                self.post_pic_to_server()
            except:
                self.plugin.sentry.captureException()

    def web_snapshot_request(self, url):
        snapshot = capture_jpeg({'snapshot': url, "snapshotSslValidation": False}, use_nozzle_config=True)
        base64_image = base64.b64encode(snapshot).decode('utf-8')
        return {'pic': base64_image}