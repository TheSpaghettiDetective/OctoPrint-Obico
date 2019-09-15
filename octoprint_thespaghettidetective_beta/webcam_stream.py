import io
import os
import logging
import subprocess
import time
import sarge
import sys
import flask
from collections import deque
import Queue
from threading import Thread, RLock
import requests
import yaml
from raven import breadcrumbs
import tempfile
import backoff
import json

from .utils import pi_version
from .ws import WebSocketClient

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective_beta')

FFMPEG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'ffmpeg')
JANUS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'janus')

class WebcamServer:
    def __init__(self, camera):
        self.camera = camera
        self.img_q = Queue.Queue(maxsize=1)
        self.last_capture = 0
        self._mutex = RLock()

    def capture_forever(self):

        bio = io.BytesIO()
        for foo in self.camera.capture_continuous(bio, format='jpeg', use_video_port=True):
            bio.seek(0)
            chunk = bio.read()
            bio.seek(0)
            bio.truncate()

            with self._mutex:
                last_last_capture = self.last_capture
                self.last_capture = time.time()

            self.img_q.put(chunk)

    def mjpeg_generator(self, boundary):
      try:
        hdr = '--%s\r\nContent-Type: image/jpeg\r\n' % boundary

        prefix = ''
        while True:
            chunk = self.img_q.get()
            msg = prefix + hdr + 'Content-Length: {}\r\n\r\n'.format(len(chunk))
            yield msg.encode('utf-8') + chunk
            prefix = '\r\n'
            time.sleep(0.15) # slow down mjpeg streaming so that it won't use too much cpu or bandwidth
      except GeneratorExit:
         print('closed')

    def get_snapshot(self):
        possible_stale_pics = 3
        while True:
            chunk = self.img_q.get()
            with self._mutex:
                gap = time.time() - self.last_capture
                if gap < 0.1:
                    possible_stale_pics -= 1      # Get a few pics to make sure we are not returning a stale pic, which will throw off Octolapse
                    if possible_stale_pics <= 0:
                        break

        return flask.send_file(io.BytesIO(chunk), mimetype='image/jpeg')

    def get_mjpeg(self):
        boundary='herebedragons'
        return flask.Response(flask.stream_with_context(self.mjpeg_generator(boundary)), mimetype='multipart/x-mixed-replace;boundary=%s' % boundary)

    def run_forever(self):
        webcam_server_app = flask.Flask('webcam_server')

        @webcam_server_app.route('/')
        def webcam():
            action = flask.request.args['action']
            if action == 'snapshot':
                return self.get_snapshot()
            else:
                return self.get_mjpeg()

        webcam_server_app.run(host='0.0.0.0', port=8080, threaded=True)

    def start(self):
        cam_server_thread = Thread(target=self.run_forever)
        cam_server_thread.daemon = True
        cam_server_thread.start()

        capture_thread = Thread(target=self.capture_forever)
        capture_thread.daemon = True
        capture_thread.start()


class WebcamStreamer:

    def __init__(self, plugin, sentry):
        self.janus_ws = None
        self.plugin = plugin
        self.sentry = sentry

    @backoff.on_exception(backoff.expo, Exception, max_tries=5)
    def __init_camera__(self):

	import picamera
	self.camera = picamera.PiCamera()
	self.camera.framerate=25
	#self.camera.resolution=resolution_tuple(dev_settings)
	#self.camera.hflip=dev_settings.get('flipH', False)
	#self.camera.vflip=dev_settings.get('flipV', False)

	#rotation = (90 if dev_settings.get('rotate90', False) else 0)
	#rotation += (-90 if dev_settings.get('rotate90N', False) else 0)
	#self.camera.rotation=rotation


    def video_pipeline(self):

        sarge.run('sudo service webcamd stop')

        try:
            self. __init_camera__()

            ffmpeg_cmd = '{} -re -i pipe:0 -c:v copy -bsf dump_extra -an -r 20 -f rtp rtp://0.0.0.0:8004?pkt_size=1300'.format(FFMPEG)
            FNULL = open(os.devnull, 'w')
            ffmpeg_proc = subprocess.Popen(ffmpeg_cmd.split(' '), stdin=subprocess.PIPE, stdout=FNULL, stderr=FNULL)

            self.start_janus()
            self.wait_for_janus()
            self.start_janus_ws_tunnel()

            self.webcam_server = WebcamServer(self.camera)
            self.webcam_server.start()

            self.camera.start_recording(ffmpeg_proc.stdin, format='h264', quality=23)

        except:
            sarge.run('sudo service webcamd start')   # failed to start picamera. falling back to mjpeg-streamer
            self.sentry.captureException()
            exc_type, exc_obj, exc_tb = sys.exc_info()
            _logger.error(exc_obj)
            return


    def pass_to_janus(self, msg):
        if self.janus_ws and self.janus_ws.connected():
            self.janus_ws.send_text(msg)

    def start_janus(self):

        def run_janus():
            env = dict(os.environ)
            env['LD_LIBRARY_PATH'] = JANUS_DIR + '/lib'
            janus_cmd = '{}/bin/janus --stun-server=stun.l.google.com:19302 --configs-folder={}/etc/janus'.format(JANUS_DIR, JANUS_DIR)
            subprocess.Popen(janus_cmd.split(' '), env=env)

        janus_thread = Thread(target=run_janus)
        janus_thread.setDaemon(True)
        janus_thread.start()


    @backoff.on_exception(backoff.expo, Exception, max_tries=10)
    def wait_for_janus(self):
        time.sleep(1)
        import socket
        socket.socket().connect(('127.0.0.1', 8188))


    def start_janus_ws_tunnel(self):
        def on_error(ws, error):
            print(error)

        def on_message(ws, msg):
            self.plugin.ss.send_text(json.dumps(dict(janus=msg)))

        self.janus_ws = WebSocketClient('ws://127.0.0.1:8188/', on_ws_msg=on_message, subprotocols=['janus-protocol'])
        wst = Thread(target=self.janus_ws.run)
        wst.daemon = True
        wst.start()


if __name__ == "__main__":

    streamer = WebcamStreamer()
    streamer.start_video_pipeline(None)
    while True:
        time.sleep(10)
