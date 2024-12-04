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

from .utils import ExpoBackoff, pi_version, is_port_open, wait_for_port, wait_for_port_to_close, run_in_thread
from .ws import WebSocketClient
from .lib import alert_queue
from .janus_config_builder import RUNTIME_JANUS_ETC_DIR

_logger = logging.getLogger('octoprint.plugins.obico')

JANUS_WS_PORT = 17730   # Janus needs to use 17730 up to 17750. Hard-coded for now. may need to make it dynamic if the problem of port conflict is too much

def janus_pid_file_path():
    global JANUS_WS_PORT
    return '/tmp/obico-janus-{janus_port}.pid'.format(janus_port=JANUS_WS_PORT)

# Make sure the port is available in case there are multiple obico instances running
for i in range(0, 100):
    if os.path.exists(janus_pid_file_path()):
        JANUS_WS_PORT += 20 # 20 is a big-enough gap for all ports needed for 1 octoprint instance.

JANUS_ADMIN_WS_PORT = JANUS_WS_PORT + 1

class JanusConn:

    def __init__(self, plugin, janus_server):
        self.plugin = plugin
        self.janus_server = janus_server
        self.janus_ws = None
        self.shutting_down = False

    def start(self, janus_bin_path, ld_lib_path):

        def run_janus_forever():
            try:
                janus_cmd = '{janus_bin_path} --stun-server=stun.l.google.com:19302 --configs-folder {config_folder}'.format(janus_bin_path=janus_bin_path, config_folder=RUNTIME_JANUS_ETC_DIR)
                env = {}
                if ld_lib_path:
                    env={'LD_LIBRARY_PATH': ld_lib_path + ':' + os.environ.get('LD_LIBRARY_PATH', '')}
                _logger.debug('Popen: {} {}'.format(env, janus_cmd))
                janus_proc = subprocess.Popen(janus_cmd.split(), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

                with open(janus_pid_file_path(), 'w') as pid_file:
                    pid_file.write(str(janus_proc.pid))

                while True:
                    line = to_unicode(janus_proc.stdout.readline(), errors='replace')
                    if line:
                        _logger.debug('JANUS: ' + line.rstrip())
                    else:  # line == None means the process quits
                        _logger.warn('Janus quit with exit code {}'.format(janus_proc.wait()))
                        return
            except Exception as ex:
                self.plugin.sentry.captureException()

        self.kill_janus_if_running()
        run_in_thread(run_janus_forever)
        self.wait_for_janus()
        self.start_janus_ws()

    def connected(self):
        return self.janus_ws and self.janus_ws.connected()

    def pass_to_janus(self, msg):
        if self.connected():
            self.janus_ws.send(msg)

    def wait_for_janus(self):
        time.sleep(0.2)
        wait_for_port(self.janus_server, JANUS_WS_PORT)

    def start_janus_ws(self):

        def on_close(ws, **kwargs):
            _logger.warn('Janus WS connection closed!')

        self.janus_ws = WebSocketClient(
            'ws://{}:{}/'.format(self.janus_server, JANUS_WS_PORT),
            on_ws_msg=self.process_janus_msg,
            on_ws_close=on_close,
            subprotocols=['janus-protocol'],
            waitsecs=30)

    def kill_janus_if_running(self):
        try:
            # It is possible that orphaned janus process is running (maybe previous python process was killed -9?).
            # Ensure the process is killed before launching a new one
            with open(janus_pid_file_path(), 'r') as pid_file:
                subprocess.run(['kill', pid_file.read()], check=True)
            wait_for_port_to_close(self.janus_server, JANUS_WS_PORT)
        except Exception as e:
            _logger.warning('Failed to shutdown Janus - ' + str(e))

        try:
            os.remove(janus_pid_file_path())
        except:
            pass

    def shutdown(self):
        self.shutting_down = True

        if self.janus_ws is not None:
            self.janus_ws.close()

        self.janus_ws = None

        self.kill_janus_if_running()

    def process_janus_msg(self, ws, raw_msg):
        try:
            msg = json.loads(raw_msg)
            _logger.debug('Relaying Janus msg')
            _logger.debug(msg)
            self.plugin.send_ws_msg_to_server(dict(janus=raw_msg))
        except:
            self.plugin.sentry.captureException()
