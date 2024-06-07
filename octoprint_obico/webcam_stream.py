import io
import re
import os
import logging
import subprocess
import time
import sarge
import sys
import flask
import traceback
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
import octoprint

from .utils import pi_version, ExpoBackoff, get_image_info, parse_integer_or_none
from .lib import alert_queue
from .webcam_capture import capture_jpeg, webcam_full_url
from .janus_config_builder import build_janus_config
from .janus import JanusConn


_logger = logging.getLogger('octoprint.plugins.obico')

FFMPEG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'ffmpeg')
FFMPEG = os.path.join(FFMPEG_DIR, 'run.sh')

JANUS_WS_PORT = 17730   # Janus needs to use 17730 up to 17750. Hard-coded for now. may need to make it dynamic if the problem of port conflict is too much
JANUS_ADMIN_WS_PORT = JANUS_WS_PORT + 1

RECODE_RESOLUTIONS_43 = {
    'low': (320, 240),
    'medium': (640, 480),
    'high': (960, 720),
}

RECODE_RESOLUTIONS_169 = {
    'low': (426, 240),
    'medium': (854, 480),
    'high': (1280, 720),
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
    try:
        for encoder in ['h264_omx', 'h264_v4l2m2m']:
            ffmpeg_cmd = '{} -re -i {} -pix_fmt yuv420p -vcodec {} -an -f rtp rtp://127.0.0.1:8014?pkt_size=1300'.format(FFMPEG, test_video, encoder)
            _logger.debug('Popen: {}'.format(ffmpeg_cmd))
            ffmpeg_test_proc = subprocess.Popen(ffmpeg_cmd.split(' '), stdout=FNULL, stderr=FNULL)
            if ffmpeg_test_proc.wait() == 0:
                if encoder == 'h264_omx':
                    return '-flags:v +global_header -c:v {} -bsf dump_extra'.format(encoder)  # Apparently OMX encoder needs extra param to get the stream to work
                else:
                    return '-c:v {}'.format(encoder)
    except Exception as e:
        _logger.exception('Failed to find ffmpeg h264 encoder. Exception: %s\n%s', e, traceback.format_exc())

    _logger.warn('No ffmpeg found, or ffmpeg does NOT support h264_omx/h264_v4l2m2m encoding.')
    return None


def get_webcam_configs(plugin):

    DEFAULT_WEBCAM_CONFIG = {
        'name': 'classic',
        'is_primary_camera': True,
        'target_fps': 10,
        'resolution': 'medium',
    }

    def webcam_config_dict(webcam):
        webcam_config = webcam.config.dict() # This turns out to be the best way to get a dict from octoprint.webcam.Webcam
        return {
            'displayName': webcam_config.get('displayName', 'Unknown'),
            'flipH': webcam_config.get('flipH', False),
            'flipV': webcam_config.get('flipV', False),
            'rotation': 270 if webcam_config.get('rotate90', False) else 0, # 270 = 90 degrees counterclockwise
            'stream': webcam_config.get('compat', {}).get('stream', None),
            'snapshot': webcam_config.get('compat', {}).get('snapshot', None),
            'streamRatio': webcam_config.get('compat', {}).get('streamRatio', '4:3'),
        }

    octoprint_webcams = octoprint.webcams.get_webcams()

    def cleaned_webcam_configs():

        if len(plugin._settings.get(["webcams"])) == 0:
            if 'classic' in octoprint_webcams:
                plugin._settings.set(["webcams"], [DEFAULT_WEBCAM_CONFIG,], force=True) # use 'classic' to be backward compatible
            elif octoprint_webcams:
                first_webcam_name = list(octoprint_webcams.keys())[0]
                DEFAULT_WEBCAM_CONFIG['name'] = first_webcam_name
                plugin._settings.set(["webcams"], [DEFAULT_WEBCAM_CONFIG,], force=True)

        # Make sure no 2 cameras have the same name
        deduped_webcam_configs_dict = {}
        for config in plugin._settings.get(["webcams"]):
            if config['name'] not in deduped_webcam_configs_dict:
                deduped_webcam_configs_dict[config['name']] = config

        if len(deduped_webcam_configs_dict.values()) < len(plugin._settings.get(["webcams"])):
            plugin._settings.set(["webcams"], list(deduped_webcam_configs_dict.values()), force=True)

        # Make sure there is one and only one primary camera
        webcam_configs = plugin._settings.get(["webcams"])
        primary_cameras = [config for config in webcam_configs if config.get('is_primary_camera', False)]
        if len(primary_cameras) != 1 and len(webcam_configs) > 0:
            for config in webcam_configs:
                config['is_primary_camera'] = False
            webcam_configs[0]['is_primary_camera'] = True
            plugin._settings.set(["webcams"], webcam_configs, force=True)

        return plugin._settings.get(["webcams"])

    configured_webcams = cleaned_webcam_configs()

    webcam_configs = []

    for webcam in configured_webcams:
        octoprint_webcam = octoprint_webcams.get(webcam['name'])
        if not octoprint_webcam:
            alert_queue.add_alert({
                'level': 'warning',
                'cause': 'streaming',
                'title': 'Wrong Webcam Configuration',
                'text': 'Obico can not find the webcam {}. Skipping it for streaming.'.format(webcam['name']),
                'info_url': '#',
                'buttons': ['more_info', 'never', 'ok']
            }, plugin)

            continue

        webcam_config = webcam_config_dict(octoprint_webcam)
        webcam_config.update(webcam)
        webcam_configs.append(webcam_config)

    if not webcam_configs:
        alert_queue.add_alert({
            'level': 'warning',
            'cause': 'streaming',
            'title': 'No Webcam Streams',
            'text': 'No properly configured webcam for streaming.',
            'info_url': '#',
            'buttons': ['more_info', 'never', 'ok']
        }, plugin)

    return webcam_configs


class WebcamStreamer:

    def __init__(self, plugin):
        self.plugin = plugin
        self.janus = None
        self.ffmpeg_out_rtp_ports = set()
        self.mjpeg_sock_list = []
        self.janus = None
        self.ffmpeg_proc = None
        self.shutting_down = False
        self.normalized_webcams = []

    def start(self, webcam_configs):

        if self.use_preconfigured_webcams():
            return

        self.shutdown_subprocesses()
        self.close_all_mjpeg_socks()

        self.webcams = webcam_configs
        self.find_streaming_params()
        self.assign_janus_params()

        # Now we know if we have a data channel, we can tell client_conn to start the data channel
        first_webcam_with_dataport = next((webcam for webcam in self.webcams if webcam.get('runtime', {}).get('dataport')), None)
        if first_webcam_with_dataport:
            first_webcam_with_dataport['runtime']['data_channel_available'] = True
            self.plugin.client_conn.open_data_channel(first_webcam_with_dataport['runtime']['dataport'])

        try:
            (janus_bin_path, ld_lib_path) = build_janus_config(self.webcams, self.plugin.auth_token(), JANUS_WS_PORT, JANUS_ADMIN_WS_PORT)
            if not janus_bin_path:
                _logger.error('Janus not found or not configured correctly. Quiting webcam streaming.')
                self.send_streaming_failed_event()
                self.shutdown()
                return

            self.janus = JanusConn(self.plugin, '127.0.0.1', JANUS_WS_PORT)
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

            self.normalized_webcams = [self.normalized_webcam_dict(webcam) for webcam in self.webcams]
            self.plugin.octoprint_settings_updater.update_settings()
            self.plugin.post_update_to_server()

            return (self.normalized_webcams, None)  # return value expected for a passthru target
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
            if 'runtime' not in webcam:
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

        def cap_recode_resolution(original_dimension):
            max_height = 720 if self.plugin.linked_printer.get('is_pro') else 480

            (img_w, img_h) = original_dimension

            recode_resolution = RECODE_RESOLUTIONS_43
            if float(img_w) / float(img_h) > 1.5:
                recode_resolution = RECODE_RESOLUTIONS_169

            max_height = min(recode_resolution[webcam.get('resolution', 'medium')][1], max_height)

            if original_dimension[1] > max_height:
                new_width = round(max_height * original_dimension[0] / original_dimension[1] / 2.0) * 2
                return (new_width, max_height)
            else:
                return original_dimension

        def cap_recode_fps(fps):
            if not self.plugin.linked_printer.get('is_pro'):
                fps = min(5, fps)
            if fps <= 5:
                fps += 3 # For some reason, when fps is set to 5, it looks like 2FPS. 8fps looks more like 5
            return fps

        try:
            stream_url = webcam_full_url(webcam.get("stream"))
            if not stream_url:
                raise Exception('stream_url not configured. Unable to stream the webcam.')

            (img_w, img_h) = (parse_integer_or_none(webcam['streaming_params'].get('recode_width')), parse_integer_or_none(webcam['streaming_params'].get('recode_height')))
            if not img_w or not img_h:
                _logger.warn('width and/or height not specified or invalid in streaming parameters. Getting the values from the source.')
                (img_w, img_h) = get_webcam_resolution(webcam)
            (img_w, img_h) = cap_recode_resolution((img_w, img_h))

            fps = parse_integer_or_none(webcam['streaming_params'].get('recode_fps'))
            if not fps:
                _logger.warn('FPS not specified or invalid in streaming parameters. Getting the values from the source.')
                fps = int(webcam['target_fps'])
            fps = cap_recode_fps(fps)

            bitrate = bitrate_for_dim(img_w, img_h)
            # A very rough estimate of the bitrate needed for the stream.
            sqrt_fps_diff = abs(fps - 25) ** 0.5
            bitrate = int(bitrate * (min(fps, 25.0) + sqrt_fps_diff) / 25.0)

            rtp_port = webcam['runtime']['videoport']
            self.start_ffmpeg(rtp_port, '-re -i {stream_url} -filter:v fps={fps} -b:v {bitrate} -pix_fmt yuv420p -s {img_w}x{img_h} {encoder}'.format(stream_url=stream_url, fps=fps, bitrate=bitrate, img_w=img_w, img_h=img_h, encoder=webcam['streaming_params'].get('h264_encoder')))
        except Exception:
            self.plugin.sentry.captureException()


    def start_ffmpeg(self, rtp_port, ffmpeg_args, retry_after_quit=False):
        ffmpeg_cmd = '{ffmpeg} -loglevel error {ffmpeg_args} -an -f rtp rtp://127.0.0.1:{rtp_port}?pkt_size=1300'.format(ffmpeg=FFMPEG, ffmpeg_args=ffmpeg_args, rtp_port=rtp_port)

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

            min_interval_btw_frames = 1.0 / float(webcam['target_fps'])
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
                mjpeg_sock.sendto(bytes('\r\n{}:{}\r\n'.format(len(encoded), len(jpg)), 'utf-8'), ('127.0.0.1', mjpeg_dataport)) # simple header format for client to recognize
                for chunk in [encoded[i:i+1400] for i in range(0, len(encoded), 1400)]:
                    mjpeg_sock.sendto(chunk, ('127.0.0.1', mjpeg_dataport))
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
                name=webcam['displayName'],
                is_primary_camera=webcam['is_primary_camera'],
                stream_mode=webcam['streaming_params'].get('mode'),
                stream_id=webcam['runtime'].get('stream_id'),
                data_channel_available=webcam['runtime'].get('data_channel_available',False),
                flipV=webcam['flipV'],
                flipH=webcam['flipH'],
                rotation=webcam['rotation'],
                streamRatio=webcam['streamRatio'],
                )

    def use_preconfigured_webcams(self):
        if os.getenv('PRECONFIGURED_WEBCAMS', '').strip() != '':
            _logger.warning('Using an external Janus gateway. Not starting the built-in Janus gateway.')
            preconfigured = json.loads(os.getenv('PRECONFIGURED_WEBCAMS'))
            self.normalized_webcams = preconfigured['webcams']
            self.plugin.octoprint_settings_updater.update_settings()
            self.plugin.post_update_to_server()

            self.janus = JanusConn(self.plugin, preconfigured['janus_server'], JANUS_WS_PORT)
            self.janus.start_janus_ws()
            return True

        return False