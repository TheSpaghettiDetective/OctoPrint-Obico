import os
import logging
import subprocess
import time

from threading import Thread
import backoff
import json
import socket
import psutil
from octoprint.util import to_unicode

try:
    import queue
except ImportError:
    import Queue as queue

from .utils import ExpoBackoff, pi_version
from .ws import WebSocketClient
from .lib import alert_queue

_logger = logging.getLogger('octoprint.plugins.obico')

JANUS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'janus')
JANUS_SERVER = os.getenv('JANUS_SERVER', '127.0.0.1')
JANUS_WS_PORT = 8188
JANUS_DATA_PORT = 8009  # check streaming plugin config
MAX_PAYLOAD_SIZE = 1500  # hardcoded in streaming plugin


class JanusConn:

    def __init__(self, plugin):
        self.plugin = plugin
        self.janus_ws_backoff = ExpoBackoff(120, max_attempts=20)
        self.janus_ws = None
        self.janus_proc = None
        self.shutting_down = False

    def start(self):
        
        if os.getenv('JANUS_SERVER', '').strip() != '':
            _logger.warning('Using an external Janus gateway. Not starting the built-in Janus gateway.')
            self.start_janus_ws()
            return

        def setup_janus_config():
            video_enabled = 'true' if self.plugin._settings.get(["disable_video_streaming"]) is not True else 'false'
            auth_token = self.plugin._settings.get(["auth_token"])

            cmd_path = os.path.join(JANUS_DIR, 'setup.sh')
            setup_cmd = '{} -A {} -V {}'.format(cmd_path, auth_token, video_enabled)

            setup_proc = psutil.Popen(setup_cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

            returncode = setup_proc.wait()
            (stdoutdata, stderrdata) = setup_proc.communicate()
            if returncode != 0:
                _logger.warning('Janus setup failed. Skipping Janus connection. Error: \n{}'.format(stdoutdata))
                return False

            return True
            

        def run_janus():
            janus_backoff = ExpoBackoff(60, max_attempts=20)
            janus_cmd = os.path.join(JANUS_DIR, 'run.sh')
            _logger.debug('Popen: {}'.format(janus_cmd))
            self.janus_proc = subprocess.Popen(janus_cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

            while not self.shutting_down:
                line = to_unicode(self.janus_proc.stdout.readline(), errors='replace')
                if line:
                    _logger.debug('JANUS: ' + line.rstrip())
                elif not self.shutting_down:
                    self.janus_proc.wait()
                    msg = 'Janus quit! This should not happen. Exit code: {}'.format(self.janus_proc.returncode)
                    self.plugin.sentry.captureMessage(msg)
                    alert_queue.add_alert({
                        'level': 'warning',
                        'cause': 'streaming',
                        'title': 'Webcam Streaming Failed',
                        'text': 'The webcam streaming failed to start. Obico is now streaming your webcam at 0.1 FPS.',
                        'info_url': 'https://www.obico.io/docs/user-guides/warnings/webcam-streaming-failed-to-start/',
                        'buttons': ['more_info', 'never', 'ok']
                    }, self.plugin, post_to_server=True)

                    janus_backoff.more(Exception(msg))
                    self.janus_proc = subprocess.Popen(janus_cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        if not setup_janus_config():
            return

        janus_proc_thread = Thread(target=run_janus)
        janus_proc_thread.daemon = True
        janus_proc_thread.start()

        self.wait_for_janus()
        self.start_janus_ws()

    def pass_to_janus(self, msg):
        if self.janus_ws and self.janus_ws.connected():
            self.janus_ws.send(msg)

    @backoff.on_exception(backoff.expo, Exception, max_tries=10)
    def wait_for_janus(self):
        time.sleep(1)
        socket.socket().connect((JANUS_SERVER, JANUS_WS_PORT))

    def start_janus_ws(self):

        def on_close(ws, **kwargs):
            self.janus_ws_backoff.more(Exception('Janus WS connection closed!'))
            if not self.shutting_down:
                _logger.warning('Reconnecting to Janus WS.')
                self.start_janus_ws()

        def on_message(ws, msg):
            if self.process_janus_msg(msg):
                self.janus_ws_backoff.reset()

        self.janus_ws = WebSocketClient(
            'ws://{}:{}/'.format(JANUS_SERVER, JANUS_WS_PORT),
            on_ws_msg=on_message,
            on_ws_close=on_close,
            subprotocols=['janus-protocol'],
            waitsecs=5)

    def shutdown(self):
        self.shutting_down = True

        if self.janus_ws is not None:
            self.janus_ws.close()

        self.janus_ws = None

        if self.janus_proc:
            try:
                self.janus_proc.terminate()
            except Exception:
                pass

        self.janus_proc = None

    def process_janus_msg(self, raw_msg):
        try:
            msg = json.loads(raw_msg)

            # when plugindata.data.obico is set, this is a incoming message from webrtc data channel
            # https://github.com/TheSpaghettiDetective/janus-gateway/commit/e0bcc6b40f145ce72e487204354486b2977393ea
            to_plugin = msg.get('plugindata', {}).get('data', {}).get('thespaghettidetective', {})

            if to_plugin:
                _logger.debug('Processing WebRTC data channel msg from client:')
                _logger.debug(msg)
                self.plugin.client_conn.on_message_to_plugin(to_plugin)
                return

            _logger.debug('Relaying Janus msg')
            _logger.debug(msg)
            self.plugin.send_ws_msg_to_server(dict(janus=raw_msg))
        except:
            self.plugin.sentry.captureException()
