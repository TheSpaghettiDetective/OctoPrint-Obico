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
from urllib.error import URLError, HTTPError
import requests
import backoff
import json
import socket
import errno
import base64
from textwrap import wrap
import psutil
from octoprint.util import to_unicode

from .utils import pi_version, ExpoBackoff, get_image_info, wait_for_port, wait_for_port_to_close, octoprint_webcam_settings
from .lib import alert_queue
from .webcam_capture import capture_jpeg, webcam_full_url
from .janus_config_builder import build_janus_config
from .janus import JanusConn


_logger = logging.getLogger('octoprint.plugins.obico')

GST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'gst')

JANUS_SERVER = os.getenv('JANUS_SERVER', '127.0.0.1')
FFMPEG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'ffmpeg')
FFMPEG = os.path.join(FFMPEG_DIR, 'run.sh')

JANUS_WS_PORT = 17730   # Janus needs to use 17730 up to 17750. Hard-coded for now. may need to make it dynamic if the problem of port conflict is too much
JANUS_ADMIN_WS_PORT = JANUS_WS_PORT + 1


PI_CAM_RESOLUTIONS = {
    'low': ((320, 240), (480, 270)),  # resolution for 4:3 and 16:9
    'medium': ((640, 480), (960, 540)),
    'high': ((1296, 972), (1640, 922)),
    'ultra_high': ((1640, 1232), (1920, 1080)),
}


def bitrate_for_dim(img_w, img_h):
    dim = img_w * img_h
    if dim <= 480 * 270:
        return 400*1000
    if dim <= 960 * 540:
        return 1300*1000
    if dim <= 1280 * 720:
        return 2000*1000
    else:
        return 3000*1000


@backoff.on_exception(backoff.expo, Exception, max_tries=3)
def get_webcam_resolution(webcam_config):
    (img_w, img_h) = (640, 360)
    try:
        (_, img_w, img_h) = get_image_info(capture_jpeg(webcam_config, force_stream_url=True))
        _logger.debug(f'Detected webcam resolution - w:{img_w} / h:{img_h}')
    except Exception:
        _logger.exception('Failed to connect to webcam to retrieve resolution. Using default.')

    return (img_w, img_h)


def find_ffmpeg_h264_encoder():
    test_video = os.path.join(FFMPEG_DIR, 'test-video.mp4')
    FNULL = open(os.devnull, 'w')
    for encoder in ['h264_omx', 'h264_v4l2m2m']:
        ffmpeg_cmd = '{} -re -i {} -pix_fmt yuv420p -vcodec {} -an -f rtp rtp://127.0.0.1:8014?pkt_size=1300'.format(FFMPEG, test_video, encoder)
        _logger.debug('Popen: {}'.format(ffmpeg_cmd))
        ffmpeg_test_proc = subprocess.Popen(ffmpeg_cmd.split(' '), stdout=FNULL, stderr=FNULL)
        if ffmpeg_test_proc.wait() == 0:
            if encoder == 'h264_omx':
                return '-flags:v +global_header -c:v {} -bsf dump_extra'.format(encoder)  # Apparently OMX encoder needs extra param to get the stream to work
            else:
                return '-c:v {}'.format(encoder)

    _logger.warn('No ffmpeg found, or ffmpeg does NOT support h264_omx/h264_v4l2m2m encoding.')
    return None


def cpu_watch_dog(watched_process, plugin, max, interval):

    def watch_process_cpu(watched_process, max, interval, plugin):
        while True:
            if not watched_process.is_running():
                return

            cpu_pct = watched_process.cpu_percent(interval=None)
            if cpu_pct > max:
                alert_queue.add_alert({
                    'level': 'warning',
                    'cause': 'cpu',
                    'title': 'Streaming Excessive CPU Usage',
                    'text': 'The webcam streaming uses excessive CPU. This may negatively impact your print quality. Consider switching "compatibility mode" to "auto" or "never", or disable the webcam streaming.',
                    'info_url': 'https://www.obico.io/docs/user-guides/warnings/compatibility-mode-excessive-cpu/',
                    'buttons': ['more_info', 'never', 'ok'],
                }, plugin, post_to_server=True)

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

    def __init__(self, plugin):
        self.plugin = plugin
        self.janus = None
        self.ffmpeg_out_rtp_ports = set()
        self.mjpeg_sock_list = []
        self.janus = None
        self.ffmpeg_proc = None
        self.shutting_down = False

    def start(self, webcam_configs):

        self.shutdown_subprocesses()
        self.close_all_mjpeg_socks()

        self.webcams = webcam_configs
        self.find_streaming_params()
        self.assign_janus_params()
        try:
            (janus_bin_path, ld_lib_path) = build_janus_config(self.webcams, self.plugin.auth_token(), JANUS_WS_PORT, JANUS_ADMIN_WS_PORT)
            if not janus_bin_path:
                _logger.error('Janus not found or not configured correctly. Quiting webcam streaming.')
                self.send_streaming_failed_event()
                self.shutdown()
                return

            self.janus = JanusConn(self.plugin)
            self.janus.start(janus_bin_path, ld_lib_path)

            if not self.wait_for_janus():
                for webcam in self.webcams:
                    webcam['error'] = 'Janus failed to start'

            for webcam in self.webcams:
                if webcam['streaming_params']['mode'] == 'h264_transcode':
                    self.h264_transcode(webcam)
                elif webcam['streaming_params']['mode'] == 'mjpeg_webrtc':
                    self.mjpeg_webrtc(webcam)
                else:
                    raise Exception('Unsupported streaming mode: {}'.format(webcam['streaming_params']['mode']))

            normalized_webcams = [self.normalized_webcam_dict(webcam) for webcam in self.webcams]
            self.printer_state.set_webcams(normalized_webcams)
            self.server_conn.post_status_update_to_server(with_settings=True)

            return (normalized_webcams, None)  # return value expected for a passthru target
        except Exception:
            self.plugin.sentry.captureException()
            _logger.error('Error. Quitting webcam streaming.', exc_info=True)
            self.send_streaming_failed_event()
            self.shutdown()
            return


    def shutdown(self):
        self.shutting_down = True
        self.shutdown_subprocesses()
        self.close_all_mjpeg_socks()
        return ('ok', None)  # return value expected for a passthru target

    def send_streaming_failed_event(self):
        event_data = {
            'event_title': 'Obico for OctoPrint: Webcam Streaming Failed',
            'event_text': 'Follow the webcam troubleshooting guide to resolve the issue.',
            'event_class': 'WARNING',
            'event_type': 'PRINTER_ERROR',
            'info_url': 'https://obico.io/docs/user-guides/webcam-feed-is-not-showing/',
        }
        self.plugin.passthru_printer_event_to_client(event_data)
        self.plugin.post_printer_event_to_server(event_data, attach_snapshot=False, spam_tolerance_seconds=60*30)

    def find_streaming_params(self):
        ffmpeg_h264_encoder = find_ffmpeg_h264_encoder()
        webcams = []
        for webcam in self.webcams:
            stream_mode = 'h264_transcode' if ffmpeg_h264_encoder else 'mjpeg_webrtc'
            webcam['streaming_params'] = dict(
                    mode=stream_mode,
                    h264_encoder=ffmpeg_h264_encoder,
            )

    def assign_janus_params(self):
        first_h264_webcam = next(filter(lambda item: 'h264' in item['streaming_params']['mode'] and item['is_primary_camera'], self.webcams), None)
        if first_h264_webcam:
            first_h264_webcam['runtime'] = {}
            first_h264_webcam['runtime']['stream_id'] = 1  # Set janus id to 1 for the first h264 stream to be compatible with old mobile app versions

        first_mjpeg_webcam = next(filter(lambda item: 'mjpeg' in item['streaming_params']['mode'] and item['is_primary_camera'], self.webcams), None)
        if first_mjpeg_webcam:
            first_mjpeg_webcam['runtime'] = {}
            first_mjpeg_webcam['runtime']['stream_id'] = 2  # Set janus id to 2 for the first mjpeg stream to be compatible with old mobile app versions

        cur_stream_id = 3
        cur_port_num = JANUS_ADMIN_WS_PORT + 1
        for webcam in self.webcams:
            if not hasattr(webcam, 'runtime'):
                webcam['runtime'] = {}

            if not webcam['runtime'].get('stream_id'):
                webcam['runtime']['stream_id'] = cur_stream_id
                cur_stream_id += 1

            if webcam['streaming_params']['mode'] == 'h264_rtsp':
                 webcam['runtime']['dataport'] = cur_port_num
                 cur_port_num += 1
            elif webcam['streaming_params']['mode'] in ('h264_copy', 'h264_transcode', 'h264_device'):
                 webcam['runtime']['videoport'] = cur_port_num
                 cur_port_num += 1
                 webcam['runtime']['videortcpport'] = cur_port_num
                 cur_port_num += 1
                 webcam['runtime']['dataport'] = cur_port_num
                 cur_port_num += 1
            elif webcam['streaming_params']['mode'] == 'mjpeg_webrtc':
                 webcam['runtime']['mjpeg_dataport'] = cur_port_num
                 cur_port_num += 1


    def wait_for_janus(self):
        for i in range(100):
            time.sleep(0.1)
            if self.janus and self.janus.janus_ws and self.janus.janus_ws.connected():
                return True

        return False


    def h264_transcode(self, webcam):

        try:
            stream_url = webcam.stream_url
            if not stream_url:
                raise Exception('stream_url not configured. Unable to stream the webcam.')

            (img_w, img_h) = (parse_integer_or_none(webcam['streaming_params'].get('recode_width')), parse_integer_or_none(webcam['streaming_params'].get('recode_height')))
            if not img_w or not img_h:
                _logger.warn('width and/or height not specified or invalid in streaming parameters. Getting the values from the source.')
                (img_w, img_h) = get_webcam_resolution(webcam)

            fps = parse_integer_or_none(webcam['streaming_params'].get('recode_fps'))
            if not fps:
                _logger.warn('FPS not specified or invalid in streaming parameters. Getting the values from the source.')
                fps = webcam.target_fps

            bitrate = bitrate_for_dim(img_w, img_h)
            if not self.is_pro:
                fps = min(8, fps) # For some reason, when fps is set to 5, it looks like 2FPS. 8fps looks more like 5
                bitrate = int(bitrate/2)

            rtp_port = webcam['runtime']['videoport']
            self.start_ffmpeg(rtp_port, '-re -i {stream_url} -filter:v fps={fps} -b:v {bitrate} -pix_fmt yuv420p -s {img_w}x{img_h} {encoder}'.format(stream_url=stream_url, fps=fps, bitrate=bitrate, img_w=img_w, img_h=img_h, encoder=webcam['streaming_params'].get('h264_encoder')))
        except Exception:
            self.plugin.sentry.captureException()


    def start_ffmpeg(self, rtp_port, ffmpeg_args, retry_after_quit=False):
        ffmpeg_cmd = '{ffmpeg} -loglevel error {ffmpeg_args} -an -f rtp rtp://{janus_server}:{rtp_port}?pkt_size=1300'.format(ffmpeg=FFMPEG, ffmpeg_args=ffmpeg_args, janus_server=JANUS_SERVER, rtp_port=rtp_port)

        _logger.debug('Popen: {}'.format(ffmpeg_cmd))
        FNULL = open(os.devnull, 'w')
        ffmpeg_proc = subprocess.Popen(ffmpeg_cmd.split(' '), stdin=subprocess.PIPE, stdout=FNULL, stderr=subprocess.PIPE)

        self.ffmpeg_out_rtp_ports.add(str(rtp_port))

        with open(self.ffmpeg_pid_file_path(rtp_port), 'w') as pid_file:
            pid_file.write(str(ffmpeg_proc.pid))

        try:
            returncode = ffmpeg_proc.wait(timeout=10) # If ffmpeg fails, it usually does so without 10s
            (stdoutdata, stderrdata) = ffmpeg_proc.communicate()
            msg = 'STDOUT:\n{}\nSTDERR:\n{}\n'.format(stdoutdata, stderrdata)
            _logger.error(msg)
            raise Exception('ffmpeg failed! Exit code: {}'.format(returncode))
        except subprocess.TimeoutExpired:
           pass

        def monitor_ffmpeg_process(ffmpeg_proc, retry_after_quit=False):
            # It seems important to drain the stderr output of ffmpeg, otherwise the whole process will get clogged
            ring_buffer = deque(maxlen=50)
            ffmpeg_backoff = ExpoBackoff(3)
            while True:
                line = to_unicode(ffmpeg_proc.stderr.readline(), errors='replace')
                if not line:  # line == None means the process quits
                    if self.shutting_down:
                        return

                    returncode = ffmpeg_proc.wait()
                    msg = 'STDERR:\n{}\n'.format('\n'.join(ring_buffer))
                    _logger.debug(msg)

                    if retry_after_quit:
                        ffmpeg_backoff.more('ffmpeg exited un-expectedly. Exit code: {}'.format(returncode))
                        ring_buffer = deque(maxlen=50)
                        _logger.debug('Popen: {}'.format(ffmpeg_cmd))
                        ffmpeg_proc = subprocess.Popen(ffmpeg_cmd.split(' '), stdin=subprocess.PIPE, stdout=FNULL, stderr=subprocess.PIPE)
                    else:
                        self.plugin.sentry.captureMessage('ffmpeg exited un-expectedly. Exit code: {}'.format(returncode))
                        return
                else:
                    ring_buffer.append(line)

        ffmpeg_thread = Thread(target=monitor_ffmpeg_process, kwargs=dict(ffmpeg_proc=ffmpeg_proc, retry_after_quit=retry_after_quit))
        ffmpeg_thread.daemon = True
        ffmpeg_thread.start()

    def mjpeg_webrtc(self, webcam):

        @backoff.on_exception(backoff.expo, Exception)
        def mjpeg_loop():

            mjpeg_dataport = webcam['runtime']['mjpeg_dataport']

            min_interval_btw_frames = 1.0 / webcam.target_fps
            bandwidth_throttle = 0.004
            if pi_version() == "0":    # If Pi Zero
                bandwidth_throttle *= 2

            mjpeg_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.mjpeg_sock_list.append(mjpeg_sock)

            last_frame_sent = 0

            while True:
                if self.shutting_down:
                    return

                time.sleep( max(last_frame_sent + min_interval_btw_frames - time.time(), 0) )
                last_frame_sent = time.time()

                jpg = None
                try:
                    jpg = capture_jpeg(webcam)
                except Exception as e:
                    _logger.warning('Failed to capture jpeg - ' + str(e))

                if not jpg:
                    continue

                encoded = base64.b64encode(jpg)
                mjpeg_sock.sendto(bytes('\r\n{}:{}\r\n'.format(len(encoded), len(jpg)), 'utf-8'), (JANUS_SERVER, mjpeg_dataport)) # simple header format for client to recognize
                for chunk in [encoded[i:i+1400] for i in range(0, len(encoded), 1400)]:
                    mjpeg_sock.sendto(chunk, (JANUS_SERVER, mjpeg_dataport))
                    time.sleep(bandwidth_throttle)

        mjpeg_loop_thread = Thread(target=mjpeg_loop)
        mjpeg_loop_thread.daemon = True
        mjpeg_loop_thread.start()

    def ffmpeg_pid_file_path(self, rtp_port):
        return '/tmp/obico-ffmpeg-{rtp_port}.pid'.format(rtp_port=rtp_port)

    def kill_all_ffmpeg_if_running(self):
        for rtc_port in self.ffmpeg_out_rtp_ports:
            self.kill_ffmpeg_if_running(rtc_port)

        self.ffmpeg_out_rtp_ports = set()

    def kill_ffmpeg_if_running(self, rtc_port):
        # It is possible that some orphaned ffmpeg process is running (maybe previous python process was killed -9?).
        # Ensure all ffmpeg processes are killed
        with open(self.ffmpeg_pid_file_path(rtc_port), 'r') as pid_file:
            try:
                subprocess.run(['kill', pid_file.read()], check=True)
            except Exception as e:
                _logger.warning('Failed to shutdown ffmpeg - ' + str(e))

    def shutdown_subprocesses(self):
        if self.janus:
            self.janus.shutdown()
        self.kill_all_ffmpeg_if_running()

    def close_all_mjpeg_socks(self):
        for mjpeg_sock in self.mjpeg_sock_list:
            mjpeg_sock.close()

    def normalized_webcam_dict(self, webcam):
        return dict(
                name=webcam.name,
                is_primary_camera=webcam['is_primary_camera'],
                is_nozzle_camera=webcam.is_nozzle_camera,
                stream_mode=webcam['streaming_params'].get('mode'),
                stream_id=webcam['runtime'].get('stream_id'),
                flipV=webcam.flip_v,
                flipH=webcam.flip_h,
                rotation=webcam.rotation,
                streamRatio='16:9' if webcam.aspect_ratio_169 else '4:3',
                )




































    @backoff.on_exception(backoff.expo, Exception)
    def mjpeg_loop(self):
        bandwidth_throttle = 0.004
        if pi_version() == "0":    # If Pi Zero
            bandwidth_throttle *= 2

        self.mjpeg_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        last_frame_sent = 0

        while True:
            if self.shutting_down:
                return

            if not self.janus.connected():
                time.sleep(1)
                continue

            time.sleep( max(last_frame_sent+0.5-time.time(), 0) )  # No more than 1 frame per 0.5 second

            jpg = None
            try:
                jpg = capture_jpeg(self.plugin)
            except Exception as e:
                _logger.warning('Failed to capture jpeg - ' + str(e))

            if not jpg:
                continue

            encoded = base64.b64encode(jpg)
            self.mjpeg_sock.sendto(bytes('\r\n{}:{}\r\n'.format(len(encoded), len(jpg)), 'utf-8'), (JANUS_SERVER, JANUS_MJPEG_DATA_PORT)) # simple header format for client to recognize
            for chunk in [encoded[i:i+1400] for i in range(0, len(encoded), 1400)]:
                self.mjpeg_sock.sendto(chunk, (JANUS_SERVER, JANUS_MJPEG_DATA_PORT))
                time.sleep(bandwidth_throttle)

        last_frame_sent = time.time()

    def video_pipeline(self):
        if not pi_version():
            self.mjpeg_loop()
            return

        try:
            if not self.plugin.is_pro_user():
                self.ffmpeg_from_mjpeg()
                return

            # camera-stream is introduced in OctoPi 1.0.
            try:
                self.compat_streaming = True # If we are streaming from camera-streamer, it should be considered as compatibility mode
                camera_streamer_mp4_url = 'http://127.0.0.1:8080/video.mp4'
                _logger.info('Trying to start ffmpeg using camera-stream H.264 source')
                # There seems to be a bug in camera-streamer that causes to close .mp4 connection after a random period of time. In that case, we rerun ffmpeg
                self.start_ffmpeg('-re -i {} -c:v copy'.format(camera_streamer_mp4_url), retry_after_quit=True)
                return
            except Exception:
                _logger.info('No camera-stream H.264 source found. Continue to legacy streaming')
                pass

            # The streaming mechansim for pre-1.0 OctoPi versions

            compatible_mode = self.plugin._settings.get(["video_streaming_compatible_mode"])
            self.compat_streaming = False

            if compatible_mode == 'auto':
                try:
                    octolapse_enabled = is_octolapse_enabled(self.plugin)
                    if octolapse_enabled:
                        _logger.warning('Octolapse is enabled. Switching to compat mode.')
                        compatible_mode = 'always'
                        alert_queue.add_alert({
                            'level': 'warning',
                            'cause': 'octolapse_compat_mode',
                            'text': 'Octolapse plugin detected! Obico has switched to "Premium (compatibility)" streaming mode.',
                            'buttons': ['never', 'ok']
                        }, self.plugin)
                except Exception:
                    self.plugin.sentry.captureException()

            if compatible_mode == 'always':
                self.ffmpeg_from_mjpeg()
                return

            if sarge.run('sudo service webcamd stop').returncodes[0] != 0:
                self.ffmpeg_from_mjpeg()
                return

            self.init_legacy_picamera()

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

                self.webcam_server = UsbCamWebServer(self.plugin.sentry)
                self.webcam_server.start()

                self.start_gst_memory_guard()

            # Use ffmpeg for Pi Camera. When it's used for USB Camera it has problems (SPS/PPS not sent in-band?)
            else:
                self.start_ffmpeg('-re -i pipe:0 -flags:v +global_header -c:v copy -bsf dump_extra')

                self.webcam_server = PiCamWebServer(self.pi_camera, self.plugin.sentry)
                self.webcam_server.start()
                self.pi_camera.start_recording(self.ffmpeg_proc.stdin, format='h264', quality=23, intra_period=25, profile='baseline')
                self.pi_camera.wait_recording(0)
        except Exception:
            alert_queue.add_alert({
                'level': 'warning',
                'cause': 'streaming',
                'title': 'Webcam Streaming Failed',
                'text': 'The webcam streaming failed to start. Obico is now streaming at 0.1 FPS.',
                'info_url': 'https://www.obico.io/docs/user-guides/warnings/webcam-streaming-failed-to-start/',
                'buttons': ['more_info', 'never', 'ok']
            }, self.plugin, post_to_server=True)

            self.restore()
            self.plugin.sentry.captureException()

    @backoff.on_exception(backoff.expo, Exception, max_tries=3)
    def ffmpeg_from_mjpeg(self):

        wait_for_port_to_close('127.0.0.1', 8080)  # wait for WebcamServer to be clear of port 8080
        sarge.run('sudo service webcamd start')

        encoder = h264_encoder()

        stream_url = webcam_full_url(octoprint_webcam_settings(self.plugin._settings).get("stream", "/webcam/?action=stream"))
        if not stream_url:
            raise Exception('stream_url not configured. Unable to stream the webcam.')

        (img_w, img_h) = (640, 480)
        try:
            (img_w, img_h) = get_webcam_resolution(self.plugin)
            _logger.debug(f'Detected webcam resolution - w:{img_w} / h:{img_h}')
        except (URLError, HTTPError, requests.exceptions.RequestException):
            _logger.warn('Failed to connect to webcam to retrieve resolution. Using default.')
        except Exception:
            self.plugin.sentry.captureException()
            _logger.warn('Failed to detect webcam resolution due to unexpected error. Using default.')

        bitrate = bitrate_for_dim(img_w, img_h)
        fps = 25
        if not self.plugin.is_pro_user():
            fps = 8 # For some reason, when fps is set to 5, it looks like 2FPS. 8fps looks more like 5
            bitrate = int(bitrate/2)

        self.start_ffmpeg('-re -i {} -filter:v fps={} -b:v {} -pix_fmt yuv420p -s {}x{} {}'.format(stream_url, fps, bitrate, img_w, img_h, encoder))
        self.compat_streaming = True

    def start_ffmpeg(self, ffmpeg_args, retry_after_quit=False):
        ffmpeg_cmd = '{} -loglevel error {} -an -f rtp rtp://{}:17734?pkt_size=1300'.format(FFMPEG, ffmpeg_args, JANUS_SERVER)

        _logger.debug('Popen: {}'.format(ffmpeg_cmd))
        FNULL = open(os.devnull, 'w')
        self.ffmpeg_proc = psutil.Popen(ffmpeg_cmd.split(' '), stdin=subprocess.PIPE, stdout=FNULL, stderr=subprocess.PIPE)
        self.ffmpeg_proc.nice(10)

        try:
            returncode = self.ffmpeg_proc.wait(timeout=10) # If ffmpeg fails, it usually does so without 10s
            (stdoutdata, stderrdata) = self.ffmpeg_proc.communicate()
            msg = 'STDOUT:\n{}\nSTDERR:\n{}\n'.format(stdoutdata, stderrdata)
            _logger.error(msg)
            raise Exception('ffmpeg quit! Exit code: {}'.format(returncode))
        except psutil.TimeoutExpired:
           pass

        cpu_watch_dog(self.ffmpeg_proc, self.plugin, max=80, interval=20)

        def monitor_ffmpeg_process(retry_after_quit=False):
            # It seems important to drain the stderr output of ffmpeg, otherwise the whole process will get clogged
            ring_buffer = deque(maxlen=50)
            ffmpeg_backoff = ExpoBackoff(3)
            while True:
                err = to_unicode(self.ffmpeg_proc.stderr.readline(), errors='replace')
                if not err:  # EOF when process ends?
                    if self.shutting_down:
                        return

                    returncode = self.ffmpeg_proc.wait()
                    msg = 'STDERR:\n{}\n'.format('\n'.join(ring_buffer))
                    _logger.debug(msg)
                    self.plugin.sentry.captureMessage('ffmpeg exited un-expectedly. Exit code: {}'.format(returncode))
                    if retry_after_quit:
                        ffmpeg_backoff.more('ffmpeg exited un-expectedly. Exit code: {}'.format(returncode))
                        ring_buffer = deque(maxlen=50)
                        _logger.debug('Popen: {}'.format(ffmpeg_cmd))
                        self.ffmpeg_proc = psutil.Popen(ffmpeg_cmd.split(' '), stdin=subprocess.PIPE, stdout=FNULL, stderr=subprocess.PIPE)
                    else:
                        return
                else:
                    ring_buffer.append(err)

        ffmpeg_thread = Thread(target=monitor_ffmpeg_process, kwargs=dict(retry_after_quit=retry_after_quit))
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
                    self.plugin.sentry.captureMessage('GST exited un-expectedly. Exit code: {}'.format(returncode))
                    gst_backoff.more(Exception('GST exited un-expectedly. Exit code: {}'.format(returncode)))

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

        if self.webcam_server:
            try:
                wait_for_port('127.0.0.1', 8080)  # Wait for Flask to start running. Otherwise we will get connection refused when trying to post to '/shutdown'
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
        if self.mjpeg_sock:
            self.mjpeg_sock.close()

        if self.webcam_server:  # If self.webcam_server is not None, we have stopped webcamd and started a Flash server in the place of it. We need to reverse that process.
            # wait for WebcamServer to be clear of port 8080. Otherwise mjpg-streamer may fail to bind 127.0.0.1:8080 (it can still bind :::8080)
            wait_for_port_to_close('127.0.0.1', 8080)
            sarge.run('sudo service webcamd start')

        self.gst_proc = None
        self.ffmpeg_proc = None
        self.pi_camera = None
        self.mjpeg_sock = None


class UsbCamWebServer:

    def __init__(self, sentry):
        self.plugin.sentry = sentry
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
                self.plugin.sentry.captureException()
            raise
        except Exception:
            self.plugin.sentry.captureException()
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
            self.plugin.sentry.captureException()
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
            try:
                flask.request.environ.get('werkzeug.server.shutdown')()
            except:
                pass
            return 'Ok'

        webcam_server_app.run(port=8080, threaded=True)

    def start(self):
        cam_server_thread = Thread(target=self.run_forever)
        cam_server_thread.daemon = True
        cam_server_thread.start()


class PiCamWebServer:
    def __init__(self, camera, sentry):
        self.plugin.sentry = sentry
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
            self.plugin.sentry.captureException()
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
            self.plugin.sentry.captureException()
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
