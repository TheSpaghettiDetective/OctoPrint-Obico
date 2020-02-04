import io
import re
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
import tempfile
import backoff
import json
import socket
import base64
from textwrap import wrap

from .utils import pi_version, ExpoBackoff, get_tags
from .ws import WebSocketClient

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

CAM_EXCLUSIVE_USE = os.path.join(tempfile.gettempdir(), '.using_picam')
FFMPEG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'ffmpeg')
GST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'gst')
JANUS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'janus')

JANUS_SERVER = os.getenv('JANUS_SERVER', '127.0.0.1')

PI_CAM_RESOLUTIONS = {
    'low': ((320,240), (480, 270), 100000), # resolution for 4:3, 16:9, and bitrate limit
    'medium': ((640, 480), (960, 540), 1000000),
    'high': ((1296, 972), (1640, 922), 3000000),
    'ultra_high': ((1640, 1232), (1920, 1080), 5000000),
}

class WebcamStreamer:

    def __init__(self, plugin, sentry):
        self.plugin = plugin
        self.sentry = sentry

        self.janus_ws_backoff = ExpoBackoff(120)
        self.pi_camera = None
        self.janus_ws = None
        self.webcam_server = None
        self.gst_proc = None
        self.ffmpeg_proc = None
        self.janus_proc = None
        self.webcamd_stopped = False
        self.shutting_down = False

    @backoff.on_exception(backoff.expo, Exception, max_tries=5)
    def __init_camera__(self):
        import picamera
        try:
            self.pi_camera = picamera.PiCamera()
            self.pi_camera.framerate=25
            (res_43, res_169, bitrate) = PI_CAM_RESOLUTIONS[self.plugin._settings.get(["pi_cam_resolution"])]
            self.pi_camera.resolution = res_169 if self.plugin._settings.effective['webcam'].get('streamRatio', '4:3') == '16:9' else res_43
            self.bitrate = bitrate
        except picamera.exc.PiCameraError:
            if os.path.exists('/dev/video0'):
                _logger.debug('v4l2 device found! Streaming as USB camera.')
                return
            else:
                raise

    def video_pipeline(self):
        if os.getenv('JANUS_SERVER'):  # It's a dev simulator using janus container
            self.start_janus_ws_tunnel()
            return

        if not pi_version():
            _logger.warn('Not running on a Pi. Quiting video_pipeline.')
            return

        # Wait to make sure other plugins that may use pi camera to init first, then yield to them if they are already using pi camera
        time.sleep(10)
        if os.path.exists(CAM_EXCLUSIVE_USE):
            _logger.warn('Conceding pi camera exclusive use')
            return

        try:
            sarge.run('sudo service webcamd stop')
            self.webcamd_stopped = True

            self. __init_camera__()

            # Use GStreamer for USB Camera. When it's used for Pi Camera it has problems (video is not playing. Not sure why)
            if not self.pi_camera:
                self.start_janus()
                self.webcam_server = UsbCamWebServer(self.sentry)
                self.webcam_server.start()

                self.start_gst_memory_guard()
                self.start_gst()

            # Use ffmpeg for Pi Camera. When it's used for USB Camera it has problems (SPS/PPS not sent in-band?)
            else:
                self.start_janus()
                self.start_ffmpeg()

                self.webcam_server = PiCamWebServer(self.pi_camera, self.sentry)
                self.webcam_server.start()
                self.pi_camera.start_recording(self.ffmpeg_proc.stdin, format='h264', quality=23, intra_period=5, bitrate=self.bitrate, profile='baseline')
                self.pi_camera.wait_recording(0)
        except:
            time.sleep(3)    # Wait for Flask to start running. Otherwise we will get connection refused when trying to post to '/shutdown'
            self.restore()
            self.sentry.captureException(tags=get_tags())
            exc_type, exc_obj, exc_tb = sys.exc_info()
            _logger.error(exc_obj)
            return

    def pass_to_janus(self, msg):
        if self.janus_ws and self.janus_ws.connected():
            self.janus_ws.send_text(msg)

    def start_janus(self):

        def ensure_janus_config():
            janus_conf_tmp = os.path.join(JANUS_DIR, 'etc/janus/janus.jcfg.template')
            janus_conf_path = os.path.join(JANUS_DIR, 'etc/janus/janus.jcfg')
            with open(janus_conf_tmp, "rt") as fin:
                with open(janus_conf_path, "wt") as fout:
                    for line in fin:
                        line = line.replace('{JANUS_HOME}', JANUS_DIR)
                        line = line.replace('{TURN_CREDENTIAL}', self.plugin._settings.get(["auth_token"]))
                        fout.write(line)

        def run_janus():
            janus_backoff = ExpoBackoff(60*1)
            janus_cmd = os.path.join(JANUS_DIR, 'run_janus.sh')
            _logger.debug('Popen: {}'.format(janus_cmd))
            self.janus_proc = subprocess.Popen(janus_cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

            while not self.shutting_down:
                line = self.janus_proc.stdout.readline()
                if line:
                    _logger.debug('JANUS: ' + line)
                elif not self.shutting_down:
                    self.janus_proc.wait()
                    msg = 'Janus quit! This should not happen. Exit code: {}'.format(self.janus_proc.returncode)
                    self.sentry.captureMessage(msg, tags=get_tags())
                    janus_backoff.more(msg)
                    self.janus_proc = subprocess.Popen(janus_cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        if os.getenv('JANUS_SERVER'):
            _logger.warning('Using extenal Janus gateway. Not starting Janus.')
        else:
            ensure_janus_config()
            janus_thread = Thread(target=run_janus)
            janus_thread.daemon = True
            janus_thread.start()

            self.wait_for_janus()

        self.start_janus_ws_tunnel()

    @backoff.on_exception(backoff.expo, Exception, max_tries=10)
    def wait_for_janus(self):
        time.sleep(1)
        socket.socket().connect((JANUS_SERVER, 8188))

    def start_janus_ws_tunnel(self):

        def on_close(ws):
            self.janus_ws_backoff.more(Exception('Janus WS connection closed!'))
            if not self.shutting_down:
                _logger.warn('WS tunnel closed. Restarting janus tunnel.')
                self.start_janus_ws_tunnel()

        def on_message(ws, msg):
            _logger.debug('Relaying Janus msg: {}'.format(msg))
            self.plugin.ss.send_text(json.dumps(dict(janus=msg), encoding='iso-8859-1', default=str))
            self.janus_ws_backoff.reset()

        self.janus_ws = WebSocketClient('ws://{}:8188/'.format(JANUS_SERVER), on_ws_msg=on_message, on_ws_close=on_close, subprotocols=['janus-protocol'])
        wst = Thread(target=self.janus_ws.run)
        wst.daemon = True
        wst.start()

    def start_ffmpeg(self):
        ffmpeg_cmd = '{} -re -i pipe:0 -c:v copy -bsf dump_extra -an -f rtp rtp://{}:8004?pkt_size=1300'.format(FFMPEG, JANUS_SERVER)
        _logger.debug('Popen: {}'.format(ffmpeg_cmd))
        FNULL = open(os.devnull, 'w')
        self.ffmpeg_proc = subprocess.Popen(ffmpeg_cmd.split(' '), stdin=subprocess.PIPE, stdout=FNULL, stderr=subprocess.PIPE)

        def monitor_ffmpeg_process():  # It's pointless to restart ffmpeg without calling pi_camera.record with the new input. Just capture unexpected exits not to see if it's a big problem
            ring_buffer = deque(maxlen=50)
            while True:
                err = self.ffmpeg_proc.stderr.readline()
                if not err: # EOF when process ends?
                    if self.shutting_down:
                        return

                    returncode = self.ffmpeg_proc.wait()
                    msg = 'STDERR:\n{}\n'.format('\n'.join(ring_buffer))
                    _logger.error(msg)
                    self.sentry.captureMessage('ffmpeg quit! This should not happen. Exit code: {}'.format(returncode), tags=get_tags())
                    return
                else:
                    ring_buffer.append(err)

        gst_thread = Thread(target=monitor_ffmpeg_process)
        gst_thread.daemon = True
        gst_thread.start()

    def start_gst_memory_guard(self):
        # Hack to deal with gst command that causes memory leak
        kill_leaked_gst_cmd = '{} 200000'.format(os.path.join(GST_DIR, 'kill_leaked_gst.sh'))
        _logger.debug('Popen: {}'.format(kill_leaked_gst_cmd))
        subprocess.Popen(kill_leaked_gst_cmd.split(' '))


    # gst may fail to open /dev/video0 a few times before it finally succeeds. Probably because system resources not immediately available after webcamd shuts down
    @backoff.on_exception(backoff.expo, Exception, max_tries=9)
    def start_gst(self):
        gst_cmd = os.path.join(GST_DIR, 'run_gst.sh')
        _logger.debug('Popen: {}'.format(gst_cmd))
        self.gst_proc = subprocess.Popen(gst_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for i in range(5):
            return_code = self.gst_proc.poll()
            if return_code:    # returncode will be None when it's still running, or 0 if exit successfully
                (stdoutdata, stderrdata)  = self.gst_proc.communicate()
                msg = 'STDOUT:\n{}\nSTDERR:\n{}\n'.format(stdoutdata, stderrdata)
                _logger.debug(msg)
                raise Exception('GST failed. Exit code: {}'.format(self.gst_proc.returncode))
            time.sleep(1)

        def ensure_gst_process():
            ring_buffer = deque(maxlen=50)
            gst_backoff = ExpoBackoff(60*10)
            while True:
                err = self.gst_proc.stderr.readline()
                if not err: # EOF when process ends?
                    if self.shutting_down:
                        return

                    returncode = self.gst_proc.wait()
                    msg = 'STDERR:\n{}\n'.format('\n'.join(ring_buffer))
                    _logger.debug(msg)
                    self.sentry.captureMessage('GST exited un-expectedly. Exit code: {}'.format(returncode), tags=get_tags())
                    gst_backoff.more('GST exited un-expectedly. Exit code: {}'.format(returncode))

                    ring_buffer = deque(maxlen=50)
                    gst_cmd = os.path.join(GST_DIR, 'run_gst.sh')
                    _logger.debug('Popen: {}'.format(gst_cmd))
                    self.gst_proc = subprocess.Popen(gst_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                else:
                    ring_buffer.append(err)

        gst_thread = Thread(target=ensure_gst_process)
        gst_thread.daemon = True
        gst_thread.start()

    def restore(self):
        self.shutting_down = True

        try:
            requests.post('http://127.0.0.1:8080/shutdown')
        except:
            pass
        if self.janus_proc:
            try:
                self.janus_proc.terminate()
            except:
                pass
        if self.gst_proc:
            try:
                self.gst_proc.terminate()
            except:
                pass
        if self.ffmpeg_proc:
            try:
                self.ffmpeg_proc.terminate()
            except:
                pass
        if self.pi_camera:
            # https://github.com/waveform80/picamera/issues/122
            try:
                self.pi_camera.stop_recording()
            except:
                pass
            try:
                self.pi_camera.close()
            except:
                pass

        if self.webcamd_stopped:
            sarge.run('sudo service webcamd start')   # failed to start picamera. falling back to mjpeg-streamer

        self.janus_proc = None
        self.gst_proc = None
        self.ffmpeg_proc = None
        self.pi_camera = None
        self.webcamd_stopped = False


class UsbCamWebServer:

    def __init__(self, sentry):
        self.sentry = sentry
        self.web_server = None

    def mjpeg_generator(self):
       s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
       try:
           s.connect(('127.0.0.1', 14499))
           while True:
               yield s.recv(1024)
       except GeneratorExit:
           pass
       except:
           self.sentry.captureException(tags=get_tags())
           raise
       finally:
           s.close()

    def get_mjpeg(self):
        return flask.Response(flask.stream_with_context(self.mjpeg_generator()), mimetype='multipart/x-mixed-replace;boundary=spionisto')

    def get_snapshot(self):
        return flask.send_file(io.BytesIO(self.next_jpg()), mimetype='image/jpeg')

    def next_jpg(self):
       s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
       try:
           s.connect(('127.0.0.1', 14499))
           chunk = s.recv(100)
           header = re.search(r"Content-Length: (\d+)", chunk.decode("iso-8859-1"), re.MULTILINE)
           if not header:
               raise Exception('Multiart header not found!')

           length = int(header.group(1))
           chunk = bytearray(chunk[header.end()+4:])
           while length > len(chunk):
               chunk.extend(s.recv(length-len(chunk)))
           return chunk[:length]
       except socket.timeout:
           exc_type, exc_obj, exc_tb = sys.exc_info()
           _logger.error(exc_obj)
           raise
       except:
           self.sentry.captureException(tags=get_tags())
           raise
       finally:
           s.close()

    def run_forever(self):
        webcam_server_app = flask.Flask('webcam_server')

        @webcam_server_app.route('/')
        def webcam():
            action = flask.request.args['action']
            if action == 'snapshot':
                return self.get_snapshot()
            else:
                return self.get_mjpeg()

        @webcam_server_app.route('/shutdown', methods=['POST'])
        def shutdown():
            flask.request.environ.get('werkzeug.server.shutdown')()
            return 'Ok'

        webcam_server_app.run(host='0.0.0.0', port=8080, threaded=True)

    def start(self):
        cam_server_thread = Thread(target=self.run_forever)
        cam_server_thread.daemon = True
        cam_server_thread.start()


class PiCamWebServer:
    def __init__(self, camera, sentry):
        self.sentry = sentry
        self.pi_camera = camera
        self.img_q = Queue.Queue(maxsize=1)
        self.last_capture = 0
        self._mutex = RLock()
        self.web_server = None

    def capture_forever(self):
      try:
        bio = io.BytesIO()
        for foo in self.pi_camera.capture_continuous(bio, format='jpeg', use_video_port=True):
            bio.seek(0)
            chunk = bio.read()
            bio.seek(0)
            bio.truncate()

            with self._mutex:
                last_last_capture = self.last_capture
                self.last_capture = time.time()

            self.img_q.put(chunk)
      except:
        self.sentry.captureException(tags=get_tags())
        raise

    def mjpeg_generator(self, boundary):
      try:
        hdr = '--%s\r\nContent-Type: image/jpeg\r\n' % boundary

        prefix = ''
        while True:
            chunk = self.img_q.get()
            msg = prefix + hdr + 'Content-Length: {}\r\n\r\n'.format(len(chunk))
            yield msg.encode('iso-8859-1') + chunk
            prefix = '\r\n'
            time.sleep(0.15) # slow down mjpeg streaming so that it won't use too much cpu or bandwidth
      except GeneratorExit:
        pass
      except:
        self.sentry.captureException(tags=get_tags())
        raise

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

        @webcam_server_app.route('/shutdown', methods=['POST'])
        def shutdown():
            flask.request.environ.get('werkzeug.server.shutdown')()
            return 'Ok'

        webcam_server_app.run(host='0.0.0.0', port=8080, threaded=True)

    def start(self):
        cam_server_thread = Thread(target=self.run_forever)
        cam_server_thread.daemon = True
        cam_server_thread.start()

        capture_thread = Thread(target=self.capture_forever)
        capture_thread.daemon = True
        capture_thread.start()
