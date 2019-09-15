import io
import os
import logging
import subprocess
import time
import json
import sarge
import sys
import flask
from flask_cors import CORS
import backoff
import tempfile
from threading import Thread

from .webcam_capture import capture_jpeg
from .ws import WebSocketClient
from .utils import pi_version
from .utils import ExpoBackoff

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective_beta')

POST_PIC_INTERVAL_SECONDS = 10.0
if os.environ.get('DEBUG'):
    POST_PIC_INTERVAL_SECONDS = 5.0

FFMPEG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'ffmpeg')
STREAM_CMD = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'stream_from_cam.sh')
JANUS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'janus')

TSD_TEMP_DIR = os.path.join(tempfile.gettempdir(), 'tsd-tmp')

if not os.path.exists(TSD_TEMP_DIR):
    os.mkdir(TSD_TEMP_DIR)

class MiniWebServer:
    def __init__(self, root_dir):
        self.root_dir = root_dir

    def run_forever(self):
        hls_server_app = flask.Flask('webcam_server')
        CORS(hls_server_app)

        @hls_server_app.route('/<path:path>')
        def webcam(path):
            return flask.send_from_directory(self.root_dir, path)

        hls_server_app.run(host='0.0.0.0', port=9332, threaded=True)

    def start(self):
        cam_server_thread = Thread(target=self.run_forever)
        cam_server_thread.daemon = True
        cam_server_thread.start()


class WebcamStreamer:

    def __init__(self, plugin, sentry, error_tracker):
        self.janus_ws = None

        self.plugin = plugin
        self.sentry = sentry
        self.error_tracker = error_tracker
        self.last_pic = 0

    def video_pipeline(self):

        sarge.run('sudo service webcamd stop')

        try:
            if not self.test_pi_camera():
                raise Exception("Can't obtain Pi Camera after 6 tries!")

            FNULL = open(os.devnull, 'w')
            raspivid_cmd = 'raspivid -t 0 -n -fps 20 -pf baseline -b 3000000 -w 960 -h 540 -o -'
            raspivid_proc = subprocess.Popen(raspivid_cmd.split(' '), stdout=subprocess.PIPE, stderr=FNULL)
            ffmpeg_cmd = '{} -re -i - -c:v copy -bsf dump_extra -an -r 20 -f rtp rtp://0.0.0.0:8004?pkt_size=1300 -c:v copy -an -r 20 -f hls -hls_time 2 -hls_list_size 10 -hls_delete_threshold 10 -hls_flags split_by_time+delete_segments+second_level_segment_index -strftime 1 -hls_segment_filename {}/%s-%%d.ts -hls_segment_type mpegts {}/stream.m3u8'.format(FFMPEG, TSD_TEMP_DIR, TSD_TEMP_DIR)
            subprocess.Popen(ffmpeg_cmd.split(' '), stdin=raspivid_proc.stdout, stdout=FNULL, stderr=FNULL)

            MiniWebServer(TSD_TEMP_DIR).start()

            self.start_janus()
            self.wait_for_janus()
            self.start_janus_ws_tunnel()
            #self.webcam_loop()

        except:
            sarge.run('sudo service webcamd start')   # failed to start picamera. falling back to mjpeg-streamer
            self.sentry.captureException()
            exc_type, exc_obj, exc_tb = sys.exc_info()
            _logger.error(exc_obj)
            return


    def pass_to_janus(self, msg):
        if self.janus_ws and self.janus_ws.connected():
            self.janus_ws.send_text(msg)

    @backoff.on_predicate(backoff.expo, max_tries=6)
    def test_pi_camera(self):
        self.error_tracker.attempt('webcam')
        subproc = subprocess.Popen("raspistill -o /tmp/test.jpg".split(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (stdout_str, stderr_str) = subproc.communicate()

        if not subproc.returncode == 0:
            self.error_tracker.add_connection_error('webcam')
            _logger.error('Failed to test Pi Camerea:')
            _logger.error(stderr_str)
            return False

        return True


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
            files = {'pic': capture_jpeg(self._settings.global_get(["webcam"]))}
        except:
            self.error_tracker.add_connection_error('webcam')
            return False

        resp = requests.post( endpoint, files=files, headers=self.auth_headers() )
        resp.raise_for_status()

        self.last_pic = time.time()
        return True


if __name__ == "__main__":

    streamer = WebcamStreamer()
    streamer.start_video_pipeline(None)
    while True:
        time.sleep(10)
