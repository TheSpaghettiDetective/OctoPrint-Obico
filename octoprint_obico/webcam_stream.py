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
try:
    import queue
except ImportError:
    import Queue as queue
try:
    ModuleNotFoundError
except NameError:
    ModuleNotFoundError = ImportError
from threading import Thread, RLock
import requests
import backoff
import json
import socket
import errno
import base64
from textwrap import wrap
import psutil
from octoprint.util import to_unicode

from .janus import JANUS_SERVER
from .utils import pi_version, ExpoBackoff, get_tags, using_pi_camera, not_using_pi_camera, get_image_info, wait_for_port, wait_for_port_to_close
from .lib import alert_queue
from .webcam_capture import capture_jpeg, webcam_full_url

_logger = logging.getLogger('octoprint.plugins.obico')

FFMPEG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'ffmpeg')
GST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'gst')

PI_CAM_RESOLUTIONS = {
    'low': ((320, 240), (480, 270)),  # resolution for 4:3 and 16:9
    'medium': ((640, 480), (960, 540)),
    'high': ((1296, 972), (1640, 922)),
    'ultra_high': ((1640, 1232), (1920, 1080)),
}


def bitrate_for_dim(img_w, img_h):
    dim = img_w * img_h
    if dim <= 480 * 270:
        return 1000000
    if dim <= 960 * 540:
        return 5000000
    if dim <= 1640 * 922:
        return 20000000
    else:
        return 6000000


def cpu_watch_dog(watched_process, plugin, max, interval):

    def watch_process_cpu(watched_process, max, interval, plugin):
        while True:
            if not watched_process.is_running():
                return

            cpu_pct = watched_process.cpu_percent(interval=None)
            if cpu_pct > max:
                alert_queue.add_alert({'level': 'warning', 'cause': 'cpu'}, plugin)

            time.sleep(interval)

    watch_thread = Thread(target=watch_process_cpu, args=(watched_process, max, interval, plugin))
    watch_thread.daemon = True
    watch_thread.start()


def is_octolapse_enabled(plugin):
    octolapse_plugin = plugin._plugin_manager.get_plugin_info('octolapse', True)
    if octolapse_plugin is None:
        # not installed or not enabled
        return False

    return octolapse_plugin.implementation._octolapse_settings.main_settings.is_octolapse_enabled


class WebcamStreamer:

    def __init__(self, plugin, sentry):
        self.plugin = plugin
        self.sentry = sentry

        self.pi_camera = None
        self.webcam_server = None
        self.gst_proc = None
        self.ffmpeg_proc = None
        self.shutting_down = False
        self.compat_streaming = False

    @backoff.on_exception(backoff.expo, Exception, max_tries=5)
    def __init_camera__(self):
        try:
            import picamera
            try:
                using_pi_camera()
                self.pi_camera = picamera.PiCamera()
                self.pi_camera.framerate = 20
                (res_43, res_169) = PI_CAM_RESOLUTIONS[self.plugin._settings.get(["pi_cam_resolution"])]
                self.pi_camera.resolution = res_169 if self.plugin._settings.effective['webcam'].get('streamRatio', '4:3') == '16:9' else res_43
                bitrate = bitrate_for_dim(self.pi_camera.resolution[0], self.pi_camera.resolution[1])
                _logger.debug('Pi Camera: framerate: {} - bitrate: {} - resolution: {}'.format(self.pi_camera.framerate, bitrate, self.pi_camera.resolution))
            except picamera.exc.PiCameraError:
                not_using_pi_camera()
                return
        except ModuleNotFoundError:
            _logger.warning('picamera module is not found on a Pi. Seems like an installation error.')
            return

    def video_pipeline(self):
        if not pi_version():
            _logger.warning('Not running on a Pi. Quiting video_pipeline.')
            return

        try:
            compatible_mode = self.plugin._settings.get(["video_streaming_compatible_mode"])

            if compatible_mode == 'auto':
                try:
                    octolapse_enabled = is_octolapse_enabled(self.plugin)
                    if octolapse_enabled:
                        _logger.warning('Octolapse is enabled. Switching to compat mode.')
                        compatible_mode = 'always'
                        alert_queue.add_alert({'level': 'warning', 'cause': 'octolapse_compat_mode'}, self.plugin)
                except Exception:
                    self.sentry.captureException(tags=get_tags())

            if compatible_mode == 'always' or not self.plugin.is_pro_user():
                self.ffmpeg_from_mjpeg()
                return

            sarge.run('sudo service webcamd stop')

            self.__init_camera__()

            # Use GStreamer for USB Camera. When it's used for Pi Camera it has problems (video is not playing. Not sure why)
            if not self.pi_camera:
                if not os.path.exists('/dev/video0'):
                    _logger.warning('No camera detected. Skipping webcam streaming')
                    return

                _logger.debug('v4l2 device found! Streaming as USB camera.')
                try:
                    self.start_gst()
                except Exception:
                    if compatible_mode == 'never':
                        raise
                    self.ffmpeg_from_mjpeg()
                    return

                self.webcam_server = UsbCamWebServer(self.sentry)
                self.webcam_server.start()

                self.start_gst_memory_guard()

            # Use ffmpeg for Pi Camera. When it's used for USB Camera it has problems (SPS/PPS not sent in-band?)
            else:
                self.start_ffmpeg('-re -i pipe:0 -flags:v +global_header -c:v copy')

                self.webcam_server = PiCamWebServer(self.pi_camera, self.sentry)
                self.webcam_server.start()
                self.pi_camera.start_recording(self.ffmpeg_proc.stdin, format='h264', quality=23, intra_period=25, profile='baseline')
                self.pi_camera.wait_recording(0)
        except Exception:
            not_using_pi_camera()
            alert_queue.add_alert({'level': 'warning', 'cause': 'streaming'}, self.plugin)

            wait_for_port('127.0.0.1', 8080)  # Wait for Flask to start running. Otherwise we will get connection refused when trying to post to '/shutdown'
            self.restore()
            self.sentry.captureException(tags=get_tags())

    def ffmpeg_from_mjpeg(self):

        @backoff.on_exception(backoff.expo, Exception, jitter=None, max_tries=4)
        def wait_for_webcamd(webcam_settings):
            return capture_jpeg(webcam_settings)

        wait_for_port_to_close('127.0.0.1', 8080)  # wait for WebcamServer to be clear of port 8080
        sarge.run('sudo service webcamd start')

        webcam_settings = self.plugin._settings.global_get(["webcam"])
        jpg = wait_for_webcamd(webcam_settings)
        (_, img_w, img_h) = get_image_info(jpg)
        stream_url = webcam_full_url(webcam_settings.get("stream", "/webcam/?action=stream"))
        bitrate = bitrate_for_dim(img_w, img_h)
        fps = 25 if self.plugin.is_pro_user() else 5

        self.start_ffmpeg('-re -i {} -filter:v fps={} -b:v {} -pix_fmt yuv420p -s {}x{} -flags:v +global_header -vcodec h264_omx'.format(stream_url, fps, bitrate, img_w, img_h))
        self.compat_streaming = True

    def start_ffmpeg(self, ffmpeg_args):
        ffmpeg_cmd = '{} {} -bsf dump_extra -an -f rtp rtp://{}:8004?pkt_size=1300'.format(FFMPEG, ffmpeg_args, JANUS_SERVER)

        _logger.debug('Popen: {}'.format(ffmpeg_cmd))
        FNULL = open(os.devnull, 'w')
        self.ffmpeg_proc = psutil.Popen(ffmpeg_cmd.split(' '), stdin=subprocess.PIPE, stdout=FNULL, stderr=subprocess.PIPE)
        self.ffmpeg_proc.nice(10)

        cpu_watch_dog(self.ffmpeg_proc, self.plugin, max=80, interval=20)

        def monitor_ffmpeg_process():  # It's pointless to restart ffmpeg without calling pi_camera.record with the new input. Just capture unexpected exits not to see if it's a big problem
            ring_buffer = deque(maxlen=50)
            while True:
                err = to_unicode(self.ffmpeg_proc.stderr.readline(), errors='replace')
                if not err:  # EOF when process ends?
                    if self.shutting_down:
                        return

                    returncode = self.ffmpeg_proc.wait()
                    msg = 'STDERR:\n{}\n'.format('\n'.join(ring_buffer))
                    _logger.error(msg)
                    self.sentry.captureMessage('ffmpeg quit! This should not happen. Exit code: {}'.format(returncode), tags=get_tags())
                    return
                else:
                    ring_buffer.append(err)

        ffmpeg_thread = Thread(target=monitor_ffmpeg_process)
        ffmpeg_thread.daemon = True
        ffmpeg_thread.start()

    def start_gst_memory_guard(self):
        # Hack to deal with gst command that causes memory leak
        kill_leaked_gst_cmd = '{} 200000'.format(os.path.join(GST_DIR, 'gst_memory_guard.sh'))
        _logger.debug('Popen: {}'.format(kill_leaked_gst_cmd))
        subprocess.Popen(kill_leaked_gst_cmd.split(' '))

    # gst may fail to open /dev/video0 a few times before it finally succeeds. Probably because system resources not immediately available after webcamd shuts down

    @backoff.on_exception(backoff.expo, Exception, jitter=None, max_tries=6)
    def start_gst(self):
        gst_cmd = os.path.join(GST_DIR, 'run_gst.sh')
        _logger.debug('Popen: {}'.format(gst_cmd))
        self.gst_proc = subprocess.Popen(gst_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for i in range(5):
            return_code = self.gst_proc.poll()
            if return_code:    # returncode will be None when it's still running, or 0 if exit successfully
                (stdoutdata, stderrdata) = self.gst_proc.communicate()
                msg = 'STDOUT:\n{}\nSTDERR:\n{}\n'.format(stdoutdata, stderrdata)
                _logger.debug(msg)
                raise Exception('GST failed. Exit code: {}'.format(self.gst_proc.returncode))
            time.sleep(1)

        def ensure_gst_process():
            ring_buffer = deque(maxlen=50)
            gst_backoff = ExpoBackoff(60 * 10, max_attempts=20)
            while True:
                err = to_unicode(self.gst_proc.stderr.readline(), errors='replace')
                if not err:  # EOF when process ends?
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
        except Exception:
            pass

        if self.gst_proc:
            try:
                self.gst_proc.terminate()
            except Exception:
                pass
        if self.ffmpeg_proc:
            try:
                self.ffmpeg_proc.terminate()
            except Exception:
                pass
        if self.pi_camera:
            # https://github.com/waveform80/picamera/issues/122
            try:
                self.pi_camera.stop_recording()
            except Exception:
                pass
            try:
                self.pi_camera.close()
            except Exception:
                pass

        # wait for WebcamServer to be clear of port 8080. Otherwise mjpg-streamer may fail to bind 127.0.0.1:8080 (it can still bind :::8080)
        wait_for_port_to_close('127.0.0.1', 8080)
        sarge.run('sudo service webcamd start')   # failed to start streaming. falling back to mjpeg-streamer

        self.gst_proc = None
        self.ffmpeg_proc = None
        self.pi_camera = None


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
        except socket.error as err:
            if err.errno not in [errno.ECONNREFUSED, ]:
                self.sentry.captureException(tags=get_tags())
            raise
        except Exception:
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
            cur = s.recv(100)
            chunk = b''
            n = 4
            while n > 0:
                n = n - 1
                # sometimes the mjpeg stream starts without
                # the mandatory headers
                if cur[:3] == b'\xff\xd8\xff':
                    return self._receive_jpeg(s, cur)
                chunk += cur
                time.sleep(0.01)
                cur = s.recv(100)
            chunk += cur
            return self._receive_multipart(s, chunk)
        except (socket.timeout, socket.error):
            exc_type, exc_obj, exc_tb = sys.exc_info()
            _logger.error(exc_obj)
            raise
        except Exception:
            self.sentry.captureException(tags=get_tags())
            raise
        finally:
            s.close()

    def _receive_jpeg(self, s, chunk):
        arr = bytearray()
        while chunk:
            index = chunk.find(b'\xff\xd9')
            if index > -1:
                arr.extend(chunk[:index+2])
                if (
                    b'spionisto' in arr or
                    b'Content-Length' in arr
                ):
                    raise Exception('Bad jpeg data')
                return arr
            arr.extend(chunk)
            chunk = s.recv(1024 * 64)
        # FIXME good or bad idea?
        return arr

    def _receive_multipart(self, s, chunk):
        header = re.search(r"Content-Length: (\d+)", chunk.decode("iso-8859-1"), re.MULTILINE)
        if not header:
            raise Exception('Multipart header not found!')

        chunk2 = bytearray(chunk[header.end() + 4:])
        return self._receive_jpeg(s, chunk2)

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

        webcam_server_app.run(port=8080, threaded=True)

    def start(self):
        cam_server_thread = Thread(target=self.run_forever)
        cam_server_thread.daemon = True
        cam_server_thread.start()


class PiCamWebServer:
    def __init__(self, camera, sentry):
        self.sentry = sentry
        self.pi_camera = camera
        self.img_q = queue.Queue(maxsize=1)
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
                    last_last_capture = self.last_capture  # noqa: F841 for sentry?
                    self.last_capture = time.time()

                self.img_q.put(chunk)
        except Exception:
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
                time.sleep(0.15)  # slow down mjpeg streaming so that it won't use too much cpu or bandwidth
        except GeneratorExit:
            pass
        except Exception:
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
        boundary = 'herebedragons'
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

        webcam_server_app.run(port=8080, threaded=True)

    def start(self):
        cam_server_thread = Thread(target=self.run_forever)
        cam_server_thread.daemon = True
        cam_server_thread.start()

        capture_thread = Thread(target=self.capture_forever)
        capture_thread.daemon = True
        capture_thread.start()
