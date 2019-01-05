# coding=utf-8
from __future__ import absolute_import
from datetime import datetime, timedelta
import time
import logging
import StringIO
import re
import urllib2
from urlparse import urlparse
from contextlib import closing
import requests
import backoff

_logger = logging.getLogger(__name__)

@backoff.on_exception(backoff.expo, Exception, max_value=1200)
@backoff.on_predicate(backoff.expo, max_value=1200)
def capture_jpeg(settings):
    snapshot_url = settings.get("snapshot", '').strip()
    if snapshot_url:
        if not urlparse(snapshot_url).scheme:
            snapshot_url = "http://localhost/" + re.sub(r"^\/", "", snapshot_url)

        with closing(urllib2.urlopen(snapshot_url)) as res:
            jpg = res.read()
            return "--boundarydonotcross\r\nContent-Type: image/jpeg\r\nContent-Length: {0}\r\n\r\n{1}\r\n".format(len(jpg), jpg)

    else:
        stream_url = settings.get("stream", "/webcam/?action=stream").strip()
        if not urlparse(stream_url).scheme:
            stream_url = "http://localhost/" + re.sub(r"^\/", "", stream_url)

        with closing(urllib2.urlopen(stream_url)) as res:
            chunker = MjpegStreamChunker()

            while True:
                data = res.readline()
                mjpg = chunker.findMjpegChunk(data)
                if mjpg:
                    res.close()
                    return mjpg


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
