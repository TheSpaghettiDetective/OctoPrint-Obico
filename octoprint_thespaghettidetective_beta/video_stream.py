import io
import os
import logging
import subprocess
import time
import sarge
import sys
import flask
import tempfile
from threading import Thread
from raven import breadcrumbs

from .utils import pi_version

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective_beta')

FFMPEG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'ffmpeg')
STREAM_CMD = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'stream_from_cam.sh')
JANUS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'janus')

TSD_TEMP_DIR = os.path.join(tempfile.gettempdir(), 'tsd-tmp')

if not os.path.exists(TSD_TEMP_DIR):
    os.mkdir(TSD_TEMP_DIR)

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


class WebRTCStreamer:

    def start_video_pipeline(self, sentryClient):

        def run_janus():
            env = dict(os.environ)
            env['LD_LIBRARY_PATH'] = JANUS_DIR + '/lib'
            janus_cmd = '{}/bin/janus --stun-server=stun.l.google.com:19302 --configs-folder={}/etc/janus'.format(JANUS_DIR, JANUS_DIR)
            FNULL = open(os.devnull, 'w')
            subprocess.Popen(janus_cmd.split(' '), env=env, stdout=FNULL, stderr=FNULL)

        janus_thread = Thread(target=run_janus)
        janus_thread.setDaemon(True)
        janus_thread.start()

        if not pi_version():
            self.camera = StubCamera()
            global FFMPEG
            FFMPEG = 'ffmpeg'
        else:
            sarge.run('sudo service webcamd stop')

            try:

                stream_cmd = 'sh {} {} {}'.format(STREAM_CMD, FFMPEG, TSD_TEMP_DIR)
                FNULL = open(os.devnull, 'w')
                print stream_cmd
                subprocess.Popen(stream_cmd.split(' '))
                #subprocess.Popen(stream_cmd.split(' '), stdout=FNULL, stderr=FNULL)

            except:
	        sarge.run('sudo service webcamd start')   # failed to start picamera. falling back to mjpeg-streamer
                #self.sentryClient.captureException()
                exc_type, exc_obj, exc_tb = sys.exc_info()
                import ipdb; ipdb.set_trace()
                _logger.error(exc_obj)
                return

class StubCamera:
    pass

if __name__ == "__main__":

    streamer = WebRTCStreamer()
    streamer.start_video_pipeline(None)
    while True:
        time.sleep(10)
