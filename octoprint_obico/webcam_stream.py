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
from .janus import JanusConn, JANUS_WS_PORT, JANUS_ADMIN_WS_PORT


_logger = logging.getLogger('octoprint.plugins.obico')

FFMPEG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'ffmpeg')
FFMPEG = os.path.join(FFMPEG_DIR, 'run.sh')

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
    """
    Detect available hardware H.264 encoders based on platform.
    
    Priority:
    1. Platform-specific hardware encoders (RPi, Intel, AMD)
    2. Generic VA-API (if available)
    3. None (falls back to MJPEG)
    
    Returns:
        str: FFmpeg encoder flags or None
    """
    from .hardware_detection import HardwareCapabilities
    
    test_video = os.path.join(FFMPEG_DIR, 'test-video.mp4')
    FNULL = open(os.devnull, 'w')
    
    hw_caps = HardwareCapabilities()
    platform = hw_caps.detect_platform()
    
    # Define encoder configurations per platform
    # Note: VA-API encoders return just the encoder name, filter chain is built dynamically
    ENCODER_CONFIGS = {
        'rpi': [
            ('h264_omx', '-flags:v +global_header -c:v h264_omx -bsf dump_extra'),
            ('h264_v4l2m2m', '-c:v h264_v4l2m2m'),
        ],
        'intel': [
            ('h264_vaapi', '-c:v h264_vaapi'),
            ('h264_qsv', '-c:v h264_qsv'),
        ],
        'amd': [
            ('h264_vaapi', '-c:v h264_vaapi'),
        ],
        'generic': [
            ('h264_vaapi', '-c:v h264_vaapi'),
        ]
    }
    
    encoders_to_test = ENCODER_CONFIGS.get(platform, [])
    
    # Test each encoder
    for encoder_name, encoder_flags in encoders_to_test:
        try:
            _logger.info(f'Testing {encoder_name} encoder for platform: {platform}')
            
            # Build test command with proper VA-API setup
            if 'h264_vaapi' in encoder_name:
                # VA-API needs device specified before input
                ffmpeg_args = [
                    FFMPEG, '-vaapi_device', '/dev/dri/renderD128',
                    '-re', '-i', test_video, '-t', '2',
                    '-vf', 'format=nv12,hwupload'
                ] + encoder_flags.split() + ['-an', '-f', 'null', '-']
            elif 'h264_qsv' in encoder_name:
                # QSV needs hardware device initialization
                ffmpeg_args = [
                    FFMPEG, '-init_hw_device', 'qsv=hw', '-filter_hw_device', 'hw',
                    '-re', '-i', test_video, '-t', '2',
                    '-vf', 'hwupload=extra_hw_frames=64,format=qsv'
                ] + encoder_flags.split() + ['-an', '-f', 'null', '-']
            else:
                # RPi and other encoders
                ffmpeg_args = [FFMPEG, '-re', '-i', test_video, '-t', '2'] + encoder_flags.split() + ['-an', '-f', 'null', '-']
            
            _logger.debug(f'Popen: {" ".join(ffmpeg_args)}')
            ffmpeg_test_proc = subprocess.Popen(
                ffmpeg_args, 
                stdout=FNULL, 
                stderr=subprocess.PIPE
            )
            
            try:
                returncode = ffmpeg_test_proc.wait(timeout=10)
                
                if returncode == 0:
                    _logger.info(f'Successfully detected {encoder_name} encoder')
                    return encoder_flags
                else:
                    stderr = ffmpeg_test_proc.stderr.read().decode('utf-8', errors='ignore')
                    _logger.debug(f'{encoder_name} test failed with code {returncode}: {stderr[:200]}')
                    
            except subprocess.TimeoutExpired:
                _logger.warning(f'{encoder_name} test timed out')
                ffmpeg_test_proc.kill()
                
        except Exception as e:
            _logger.debug(f'Failed to test {encoder_name}: {str(e)}')
    
    _logger.warn(f'No hardware H.264 encoder found for platform: {platform}. Falling back to MJPEG.')
    return None


def get_webcam_configs(plugin):

    DEFAULT_WEBCAM_CONFIG = {
        'name': 'classic',
        'is_primary_camera': True,
        'target_fps': 25,
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
                'text': 'Obico can not find the webcam [{}]. Skipping it for streaming.'.format(webcam['name']),
                'info_url': 'https://obico.io/docs/user-guides/multiple-cameras-octoprint/',
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
            'info_url': 'https://obico.io/docs/user-guides/multiple-cameras-octoprint/',
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
        self.webcams = []
        self.normalized_webcams = []
        self.data_channel_id = None

    def start(self, webcam_configs):

        if self.plugin._settings.get(["disable_video_streaming"]):
            _logger.info('Video streaming is disabled. Skipping webcam streaming.')
            return (webcam_configs, None)

        janus_server = '127.0.0.1'

        preconfigured = self.preconfigured_webcams()
        if preconfigured:
            self.webcams = preconfigured.get('webcams')
            janus_server = preconfigured.get('janus_server')

        if not self.webcams:

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

                self.janus = JanusConn(self.plugin, janus_server)
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

            except Exception:
                self.plugin.sentry.captureException()
                _logger.error('Error. Quitting webcam streaming.', exc_info=True)
                self.send_streaming_failed_event()
                self.shutdown()
                return

        # Now we know if we have a data channel, we can tell client_conn to start the data channel
        first_webcam_with_dataport = next((webcam for webcam in self.webcams if webcam.get('runtime', {}).get('dataport')), None)
        if first_webcam_with_dataport:
            first_webcam_with_dataport['runtime']['data_channel_available'] = True
            self.data_channel_id = first_webcam_with_dataport['runtime']['stream_id']
            self.plugin.client_conn.open_data_channel(janus_server, first_webcam_with_dataport['runtime']['dataport'])

        self.normalized_webcams = [self.normalized_webcam_dict(webcam) for webcam in self.webcams]
        self.plugin.octoprint_settings_updater.update_settings()
        self.plugin.post_update_to_server()

        return (self.normalized_webcams, None)  # return value expected for a passthru target


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
            encoder = webcam['streaming_params'].get('h264_encoder')
            
            # Build appropriate filter chain based on encoder type
            if 'h264_vaapi' in encoder:
                # VA-API needs: scale -> fps -> format -> hwupload -> encode
                filter_chain = f'fps={fps},scale={img_w}:{img_h},format=nv12,hwupload'
                ffmpeg_args = [
                    '-vaapi_device', '/dev/dri/renderD128',
                    '-re', '-i', stream_url,
                    '-vf', filter_chain,
                    '-b:v', str(bitrate)
                ] + encoder.split()
            elif 'h264_qsv' in encoder:
                # QSV needs: scale -> fps -> hwupload -> encode
                filter_chain = f'fps={fps},scale={img_w}:{img_h},hwupload=extra_hw_frames=64,format=qsv'
                ffmpeg_args = [
                    '-init_hw_device', 'qsv=hw',
                    '-filter_hw_device', 'hw',
                    '-re', '-i', stream_url,
                    '-vf', filter_chain,
                    '-b:v', str(bitrate)
                ] + encoder.split()
            else:
                # RPi encoders and fallback: standard pipeline
                ffmpeg_args = [
                    '-re', '-i', stream_url,
                    '-filter:v', f'fps={fps}',
                    '-b:v', str(bitrate),
                    '-pix_fmt', 'yuv420p',
                    '-s', f'{img_w}x{img_h}'
                ] + encoder.split()
            
            self.start_ffmpeg(rtp_port, ffmpeg_args)
        except Exception:
            self.plugin.sentry.captureException()


    @backoff.on_exception(backoff.expo, Exception, base=3, jitter=None, max_tries=5) # webcam-streamer may start after ffmpeg. We should retry in this case
    def start_ffmpeg(self, rtp_port, ffmpeg_args, retry_after_quit=False):
        # Build full command as list
        ffmpeg_cmd = [FFMPEG, '-loglevel', 'error'] + ffmpeg_args + ['-an', '-f', 'rtp', f'rtp://127.0.0.1:{rtp_port}?pkt_size=1300']

        _logger.debug('Popen: {}'.format(' '.join(ffmpeg_cmd)))
        FNULL = open(os.devnull, 'w')
        ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stdout=FNULL, stderr=subprocess.PIPE)

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

        try:
            os.remove(self.ffmpeg_pid_file_path(rtc_port))
        except:
            pass

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

    def preconfigured_webcams(self):
        preconfigured = None
        if os.getenv('PRECONFIGURED_WEBCAMS', '').strip() != '':
            _logger.warning('Using an external Janus gateway. Not starting the built-in Janus gateway.')
            preconfigured = json.loads(os.getenv('PRECONFIGURED_WEBCAMS'))

            if preconfigured:
                self.janus = JanusConn(self.plugin, preconfigured['janus_server'], JANUS_WS_PORT)
                self.janus.start_janus_ws()

        return preconfigured