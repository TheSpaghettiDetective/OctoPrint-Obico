# coding=utf-8
from __future__ import absolute_import
import bson
import logging
import threading
import sarge
import json
import re
import os
import sys
import time
import requests
import backoff
import queue
from collections import deque

from .ws import WebSocketClient, WebSocketClientException
from .commander import Commander
from .utils import (
    ExpoBackoff, SentryWrapper, pi_version,
    get_tags, not_using_pi_camera, OctoPrintSettingsUpdater, server_request)
from .lib.error_stats import error_stats
from .print_event import PrintEventTracker
from .webcam_stream import WebcamStreamer, JANUS_SERVER, JANUS_DATA_PORT
from .remote_status import RemoteStatus
from .webcam_capture import JpegPoster
from .file_download import FileDownloader
from .tunnel import LocalTunnel
from . import plugin_apis
from .udp_client import UDPClient


import octoprint.plugin

__python_version__ = 3 if sys.version_info >= (3, 0) else 2

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

POST_STATUS_INTERVAL_SECONDS = 15.0

DEFAULT_LINKED_PRINTER = {'is_pro': False}

_print_event_tracker = PrintEventTracker()


class TheSpaghettiDetectivePlugin(
        octoprint.plugin.SettingsPlugin,
        octoprint.plugin.StartupPlugin,
        octoprint.plugin.ShutdownPlugin,
        octoprint.plugin.EventHandlerPlugin,
        octoprint.plugin.AssetPlugin,
        octoprint.plugin.SimpleApiPlugin,
        octoprint.plugin.WizardPlugin,
        octoprint.plugin.TemplatePlugin,):

    def __init__(self):
        self.ss = None
        self.last_status_update_ts = 0
        self.remote_status = RemoteStatus()
        self.commander = Commander()
        self.octoprint_settings_updater = OctoPrintSettingsUpdater(self)
        self.jpeg_poster = JpegPoster(self)
        self.file_downloader = FileDownloader(self, _print_event_tracker)
        self.webcam_streamer = None
        self.linked_printer = DEFAULT_LINKED_PRINTER
        self.local_tunnel = None
        self.udp_q = queue.Queue(maxsize=1)
        self.udp_client = UDPClient(JANUS_SERVER, JANUS_DATA_PORT, self.udp_q)
        self.seen_refs = deque(maxlen=25)  # contains "last" 25 passthru refs
        self.seen_refs_lock = threading.RLock()

    # ~~ Wizard plugin mix

    def is_wizard_required(self):
        return not self._settings.get(["auth_token"])

    def get_wizard_version(self):
        return 2

    # ~~ SettingsPlugin mixin

    def get_settings_defaults(self):
        # Initialize sentry the first opportunity when `self._plugin_version` is available. Is there a better place for it?
        self.sentry = SentryWrapper(self)

        return dict(
            endpoint_prefix='https://app.thespaghettidetective.com',
            disable_video_streaming=False,
            pi_cam_resolution='medium',
            sentry_opt='out',
            video_streaming_compatible_mode='auto',
        )

    # ~~ AssetPlugin mixin

    def get_assets(self):
        # Define your plugin's asset files to automatically include in the
        # core UI here.
        return dict(
            js=["js/TheSpaghettiDetectiveSettings.js", "js/TheSpaghettiDetectiveWizard.js"],
            css=["css/TheSpaghettiDetective.css"],
            less=["less/TheSpaghettiDetective.less"]
        )

    # ~~ Softwareupdate hook

    def get_update_information(self):
        # Define the configuration for your plugin to use with the Software Update
        # Plugin here. See https://github.com/foosel/OctoPrint/wiki/Plugin:-Software-Update
        # for details.
        return dict(
            TheSpaghettiDetective=dict(
                displayName="Access Anywhere - The Spaghetti Detective",
                displayVersion=self._plugin_version,

                # version check: github repository
                type="github_release",
                user="TheSpaghettiDetective",
                repo="OctoPrint-TheSpaghettiDetective",
                current=self._plugin_version,

                # update method: pip
                pip="https://github.com/TheSpaghettiDetective/OctoPrint-TheSpaghettiDetective/archive/{target_version}.zip"
            )
        )
    # ~~ plugin APIs

    def get_api_commands(self):
        return plugin_apis.get_api_commands()

    def is_api_adminonly(self):
        return True

    def on_api_command(self, command, data):
        return plugin_apis.on_api_command(self, command, data)


    # ~~ Eventhandler mixin

    def on_event(self, event, payload):
        global _print_event_tracker

        try:
            if event == 'FirmwareData':
                self.octoprint_settings_updater.update_firmware(payload)
                self.post_printer_status(_print_event_tracker.octoprint_data(self))
            elif event == 'SettingsUpdated':
                self.octoprint_settings_updater.update_settings()
                self.post_printer_status(_print_event_tracker.octoprint_data(self))
            elif event.startswith("Print"):
                event_payload = _print_event_tracker.on_event(self, event, payload)
                if event_payload:
                    self.post_printer_status(event_payload)
        except Exception as e:
            self.sentry.captureException(tags=get_tags())
    # ~~Shutdown Plugin

    def on_shutdown(self):
        if self.ss is not None:
            self.ss.close()
        if self.webcam_streamer:
            self.webcam_streamer.restore()
        if self.udp_client:
            self.udp_client.close()

        not_using_pi_camera()

    # ~~Startup Plugin

    def on_after_startup(self):
        not_using_pi_camera()

        udp_thread = threading.Thread(target=self.udp_client.run)
        udp_thread.daemon = True
        udp_thread.start()

        main_thread = threading.Thread(target=self.main_loop)
        main_thread.daemon = True
        main_thread.start()
    # Private methods

    def auth_headers(self, auth_token=None):
        return {"Authorization": "Token " + self.auth_token(auth_token)}

    def main_loop(self):
        global _print_event_tracker

        get_tags()  # init tags to minimize risk of race condition

        self.linked_printer = self.wait_for_auth_token().get('printer', DEFAULT_LINKED_PRINTER)
        self._plugin_manager.send_plugin_message(self._identifier, {'plugin_updated': True})
        self.sentry.user_context({'id': self.auth_token()})
        _logger.info('Linked printer: {}'.format(self.linked_printer))
        _logger.debug('Plugin settings: {}'.format(self._settings.get_all_data()))

        if self.linked_printer.get('is_pro') and not self._settings.get(["disable_video_streaming"]):
            _logger.info('Starting webcam streamer')
            self.webcam_streamer = WebcamStreamer(self, self.sentry)
            stream_thread = threading.Thread(target=self.webcam_streamer.video_pipeline)
            stream_thread.daemon = True
            stream_thread.start()

        server_host = '127.0.0.1'  # FIXME
        server_port = self._settings.global_get(['server', 'port'])

        url = 'http://{}:{}'.format(server_host, server_port)
        self.local_tunnel = LocalTunnel(
            base_url=url,
            on_http_response=self.send_ws_msg_to_server,
            on_ws_message=self.send_ws_msg_to_server,
            data_dir=self.get_plugin_data_folder(),
            sentry=self.sentry)

        backoff = ExpoBackoff(120)
        while True:
            try:
                if self.last_status_update_ts < time.time() - POST_STATUS_INTERVAL_SECONDS:
                    error_stats.attempt('server')
                    self.post_printer_status(_print_event_tracker.octoprint_data(self), try_connecting=True)
                    backoff.reset()

                self.jpeg_poster.post_jpeg_if_needed()
                time.sleep(1)

            except WebSocketClientException as e:
                error_stats.add_connection_error('server', self)
                backoff.more(e)
            except Exception as e:
                self.sentry.captureException(tags=get_tags())
                error_stats.add_connection_error('server', self)
                backoff.more(e)

    def post_printer_status(self, data, try_connecting=False):
        if self.send_ws_msg_to_server(data, try_connecting=try_connecting):
            self.last_status_update_ts = time.time()

    def send_ws_msg_to_server(self, data, try_connecting=False, as_binary=False):
        """
            try_connecting: should try to connect to websocket server is not already. Only the one in the main loop should set it to True to avoid race condition
            Returns: True if message is sent successfully. Otherwise returns False.
        """
        if not self.is_configured():
            _logger.warning("Plugin not configured. Not sending message to server...")
            return False

        if as_binary:
            raw = bson.dumps(data)
            _logger.debug("Sending binary ({} bytes) to server".format(len(raw)))
        else:
            _logger.debug("Sending to server: \n{}".format(data))
            if __python_version__ == 3:
                raw = json.dumps(data, default=str)
            else:
                raw = json.dumps(data, encoding='iso-8859-1', default=str)

        if not self.ss or not self.ss.connected():
            if try_connecting:
                self.ss = WebSocketClient(self.canonical_ws_prefix() + "/ws/dev/", token=self.auth_token(), on_ws_msg=self.process_server_msg, on_ws_close=self.on_ws_close)
                self._plugin_manager.send_plugin_message(self._identifier, {'plugin_updated': True})
            else:
                return False

        self.ss.send(raw, as_binary=as_binary)

        return True

    def send_janus_msg_to_browser(self, data):
        _logger.debug("Sending to browser: \n{}".format(data))
        if __python_version__ == 3:
            raw = json.dumps(data, default=str)
        else:
            raw = json.dumps(data, encoding='iso-8859-1', default=str)

        try:
            self.udp_q.put_nowait(bytes(raw.encode()))
        except queue.Full:
            _logger.debug("udp queue is full, msg dropped")


    def on_ws_close(self, ws):
        _logger.error("Server websocket is closing")
        self._plugin_manager.send_plugin_message(self._identifier, {'plugin_updated': True})
        self.local_tunnel.close_all_octoprint_ws()
        self.ss = None

    def process_server_msg(self, ws, raw_data):
        try:
            # raw_data can be both json or bson
            # no py2 compat way to properly detect type here
            # (w/o patching ws lib)
            try:
                msg = json.loads(raw_data)
                _logger.debug('Received: ' + raw_data)
            except ValueError:
                msg = bson.loads(raw_data)
                _logger.debug(
                    'received binary message ({} bytes)'.format(len(raw_data)))

            self._process_server_msg(msg)
        except:
            self.sentry.captureException(tags=get_tags())

    def _process_server_msg(self, msg):
        global _print_event_tracker

        try:
            for command in msg.get('commands', []):
                if command["cmd"] == "pause":
                    self.commander.prepare_to_pause(
                        self._printer, **command.get('args'))
                    self._printer.pause_print()

                if command["cmd"] == 'cancel':
                    self._printer.cancel_print()

                if command["cmd"] == 'resume':
                    self._printer.resume_print()

                if command["cmd"] == 'print':
                    self.start_print(**command.get('args'))

            passthru = msg.get('passthru')
            if passthru:
                target = getattr(self, passthru.get('target'))
                func = getattr(target, passthru['func'], None)
                if not func:
                    return

                ack_ref = passthru.get('ref')
                if ack_ref is not None:
                    # same msg may arrive through both ws and datachannel
                    with self.seen_refs_lock:
                        if ack_ref in self.seen_refs:
                            _logger.debug('Got duplicate ref, ignoring msg')
                            return
                        # no need to remove item or check fullness
                        # as deque manages that when maxlen is set
                        self.seen_refs.append(ack_ref)

                ret = func(*(passthru.get("args", [])))

                if ack_ref:
                    self.send_ws_msg_to_server(
                        {'passthru': {'ref': ack_ref, 'ret': ret}})
                    # for fair play let ws go first
                    self.send_janus_msg_to_browser(
                        {'passthru': {'ref': ack_ref, 'ret': ret, '_webrtc': True}})

                time.sleep(0.2)  # chnages, such as setting temp will take a bit of time to be reflected in the status. wait for it
                self.post_printer_status(_print_event_tracker.octoprint_data(self))

            if msg.get('janus') and self.webcam_streamer:
                self.webcam_streamer.pass_to_janus(msg.get('janus'))

            if msg.get('remote_status'):
                self.remote_status.update(msg.get('remote_status'))
                if self.remote_status['viewing']:
                    self.jpeg_poster.post_jpeg_if_needed(force=True)

            if msg.get('http.tunnel') and self.local_tunnel:
                self.local_tunnel.send_http_to_local(**msg.get('http.tunnel'))

            if msg.get('ws.tunnel') and self.local_tunnel:
                kwargs = msg.get('ws.tunnel')
                kwargs['type_'] = kwargs.pop('type')
                self.local_tunnel.send_ws_to_local(**kwargs)
        except:
            self.sentry.captureException(tags=get_tags())

    def process_janus_msg(self, raw_msg):
        try:
            msg = json.loads(raw_msg)
        except ValueError:
            return False

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
            self._process_server_msg(to_plugin)
            return True

        _logger.debug('Relaying Janus msg')
        _logger.debug(msg)
        return self.send_ws_msg_to_server(dict(janus=raw_msg))

    # ~~ helper methods

    def canonical_endpoint_prefix(self):
        if not self._settings.get(["endpoint_prefix"]):
            return None

        endpoint_prefix = self._settings.get(["endpoint_prefix"]).strip()
        if endpoint_prefix.endswith('/'):
            endpoint_prefix = endpoint_prefix[:-1]
        return endpoint_prefix

    def canonical_ws_prefix(self):
        return re.sub(r'^http', 'ws', self.canonical_endpoint_prefix())

    def auth_token(self, token=None):
        t = token if token is not None else self._settings.get(["auth_token"])
        return t.strip() if t else ''

    def is_configured(self):
        return self._settings.get(["endpoint_prefix"]) and self._settings.get(["auth_token"])

    def tsd_api_status(self, auth_token=None):
        return server_request('GET', '/api/v1/octo/printer/', self, headers=self.auth_headers(auth_token=self.auth_token(auth_token)))

    @backoff.on_predicate(backoff.expo, max_value=1200)
    def wait_for_auth_token(self):
        while not self.is_configured():
            time.sleep(1)

        resp = self.tsd_api_status()
        if resp and resp.ok:
            return resp.json()
        else:
            return None # Triggers a backoff


# If you want your plugin to be registered within OctoPrint under a different name than what you defined in setup.py
# ("OctoPrint-PluginSkeleton"), you may define that here. Same goes for the other metadata derived from setup.py that
# can be overwritten via __plugin_xyz__ control properties. See the documentation for that.
__plugin_name__ = "Access Anywhere - The Spaghetti Detective"
__plugin_author__ = "TSD Team"
__plugin_url__ = "https://thespaghettidetective.com"
__plugin_description__ = "Monitor and control your printer anywhere over the internet, on your phone! No port-forwarding or VPN is needed. Best part? AI-based failure detection!"
__plugin_license__ = "AGPLv3"
__plugin_pythoncompat__ = ">=2.7,<4"


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = TheSpaghettiDetectivePlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.commander.track_gcode,
        "octoprint.comm.protocol.scripts": (__plugin_implementation__.commander.script_hook, 100000),
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
    }
