# coding=utf-8
from __future__ import absolute_import
from datetime import datetime, timedelta
import time
import logging
import StringIO
import re
import os
import urllib2
from urlparse import urlparse
from contextlib import closing
import requests
import backoff


POST_PIC_INTERVAL_SECONDS = 10.0
if os.environ.get('DEBUG'):
    POST_PIC_INTERVAL_SECONDS = 3.0

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

@backoff.on_exception(backoff.expo, Exception, max_tries=6)
@backoff.on_predicate(backoff.expo, max_tries=6)
def capture_jpeg(webcam_settings):
    snapshot_url = webcam_settings.get("snapshot", '').strip()
    snapshot_timeout = int(webcam_settings.get("snapshotTimeout", '5'))
    snapshot_validate_ssl = bool(webcam_settings.get("snapshotSslValidation", 'False'))
    if snapshot_url:
        if not urlparse(snapshot_url).scheme:
            snapshot_url = "http://localhost/" + re.sub(r"^\/", "", snapshot_url)

        r = requests.get(snapshot_url, stream=True, timeout=snapshot_timeout, verify=snapshot_validate_ssl )
        r.raise_for_status()
        jpg = r.content
        return jpg

    else:
        stream_url = webcam_settings.get("stream", "/webcam/?action=stream").strip()
        if not urlparse(stream_url).scheme:
            stream_url = "http://localhost/" + re.sub(r"^\/", "", stream_url)

        with closing(urllib2.urlopen(stream_url)) as res:
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
        self.current_chunk = StringIO.StringIO()

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

        endpoint = self.plugin.canonical_endpoint_prefix() + '/api/v1/octo/pic/'

        try:
            self.plugin.error_tracker.attempt('webcam')
            files = {'pic': capture_jpeg(self.plugin._settings.global_get(["webcam"]))}
            _logger.debug('Jpeg posted to server')
        except:
            self.plugin.error_tracker.add_connection_error('webcam')
            return

        resp = requests.post( endpoint, files=files, headers=self.plugin.auth_headers() )
        resp.raise_for_status()

        self.last_jpg_post_ts = time.time()
