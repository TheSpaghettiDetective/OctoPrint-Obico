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
import requests

from .utils import ExpoBackoff

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective_beta')

POST_PIC_INTERVAL_SECONDS = 10.0
if os.environ.get('DEBUG'):
    POST_PIC_INTERVAL_SECONDS = 5.0

class WebcamCapturer:

    def __init__(self, plugin, op_settings, error_tracker, sentry): 
        self.plugin = plugin
        self.op_settings = op_settings
        self.error_tracker = error_tracker
        self.sentry = sentry

        self.last_pic = 0

    def webcam_loop(self):

        backoff = ExpoBackoff(120)
        while True:
            if self.last_pic < time.time() - POST_PIC_INTERVAL_SECONDS:
                try:
                    self.error_tracker.attempt('server')
                    if self.post_jpg():
                        backoff.reset()
                except Exception as e:
                    self.sentry.captureException()
                    self.error_tracker.add_connection_error('server')
                    backoff.more(e)

            time.sleep(1)

    def post_jpg(self):
        if not self.plugin.is_configured():
            return True

        endpoint = self.plugin.canonical_endpoint_prefix() + '/api/octo/pic/'

        try:
            self.error_tracker.attempt('webcam')
            files = {'pic': self.capture_jpeg()}
        except:
            self.error_tracker.add_connection_error('webcam')
            return False

        resp = requests.post( endpoint, files=files, headers=self.plugin.auth_headers() )
        resp.raise_for_status()

        self.last_pic = time.time()
        return True

    @backoff.on_exception(backoff.expo, Exception, max_tries=6)
    @backoff.on_predicate(backoff.expo, max_tries=6)
    def capture_jpeg(self):
        webcam_settings = self.op_settings.global_get(["webcam"])
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
