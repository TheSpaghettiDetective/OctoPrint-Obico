import os
import logging
import subprocess
import time

from threading import Thread
import backoff
import json
import socket
from octoprint.util import to_unicode

try:
    import queue
except ImportError:
    import Queue as queue

from .utils import ExpoBackoff, get_tags, pi_version
from .ws import WebSocketClient

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

JANUS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'janus')
USE_EXTERNAL_JANUS = os.getenv('JANUS_SERVER', '').strip() != ''
JANUS_SERVER = os.getenv('JANUS_SERVER', '127.0.0.1')
JANUS_WS_PORT = 8188
JANUS_DATA_PORT = 8005  # check streaming plugin config


class JanusConn:

    def __init__(self, plugin):
        self.plugin = plugin
        self.janus_ws_backoff = ExpoBackoff(120)
        self.janus_ws = None
        self.janus_proc = None
        self.shutting_down = False

    def start(self):
        if USE_EXTERNAL_JANUS or not pi_version():
            # Maybe it's a dev simulator using janus container
            _logger.warning('Using external Janus gateway or not on a Pi. Not starting Janus.')
            self.start_janus_ws()
            return

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
            janus_backoff = ExpoBackoff(60 * 1)
            janus_cmd = os.path.join(JANUS_DIR, 'run_janus.sh')
            _logger.debug('Popen: {}'.format(janus_cmd))
            self.janus_proc = subprocess.Popen(janus_cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

            while not self.shutting_down:
                line = to_unicode(self.janus_proc.stdout.readline(), errors='replace')
                if line:
                    _logger.debug('JANUS: ' + line.rstrip())
                elif not self.shutting_down:
                    self.janus_proc.wait()
                    msg = 'Janus quit! This should not happen. Exit code: {}'.format(self.janus_proc.returncode)
                    self.plugin.sentry.captureMessage(msg, tags=get_tags())
                    janus_backoff.more(msg)
                    self.janus_proc = subprocess.Popen(janus_cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        ensure_janus_config()
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

        def on_close(ws):
            self.janus_ws_backoff.more(Exception('Janus WS connection closed!'))
            if not self.shutting_down:
                _logger.warn('Reconnecting to Janus WS.')
                self.start_janus_ws()

        def on_message(ws, msg):
            if self.process_janus_msg(msg):
                self.janus_ws_backoff.reset()

        self.janus_ws = WebSocketClient(
            'ws://{}:{}/'.format(JANUS_SERVER, JANUS_WS_PORT),
            on_ws_msg=on_message,
            on_ws_close=on_close,
            subprotocols=['janus-protocol'])

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

            # when plugindata.data.thespaghettidetective is set, this is a incoming message from webrtc data channel
            # https://github.com/TheSpaghettiDetective/janus-gateway/commit/e0bcc6b40f145ce72e487204354486b2977393ea
            to_plugin = msg.get(
                'plugindata', {}
            ).get(
                'data', {}
            ).get(
                'thespaghettidetective', {}
            )

            if to_plugin:
                _logger.debug('Processing Janus msg')
                _logger.debug(msg)
                self.plugin.client_conn.on_message_to_plugin(to_plugin)
                return

            _logger.debug('Relaying Janus msg')
            _logger.debug(msg)
            self.plugin.send_ws_msg_to_server(dict(janus=raw_msg))
        except:
            self.plugin.sentry.captureException(tags=get_tags())
