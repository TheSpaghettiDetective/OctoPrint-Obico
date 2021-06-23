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
try:
    import queue
except ImportError:
    import Queue as queue

from .ws import WebSocketClient, WebSocketConnectionException
from .commander import Commander
from .utils import (
    ExpoBackoff, SentryWrapper, pi_version,
    get_tags, not_using_pi_camera, OctoPrintSettingsUpdater, server_request)
from .lib.error_stats import error_stats
from .print_event import PrintEventTracker
from .janus import JanusConn
from .webcam_stream import WebcamStreamer
from .remote_status import RemoteStatus
from .webcam_capture import JpegPoster
from .file_download import FileDownloader
from .tunnel import LocalTunnel
from . import plugin_apis
from .client_conn import ClientConn
import zlib
from .printer_discovery import PrinterDiscovery

import octoprint.plugin

__python_version__ = 3 if sys.version_info >= (3, 0) else 2

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

POST_STATUS_INTERVAL_SECONDS = 50.0

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
        self.status_posted_to_server_ts = 0
        self.message_queue_to_server = queue.Queue(maxsize=1000)
        self.status_update_booster = 0    # update status at higher frequency when self.status_update_booster > 0
        self.status_update_lock = threading.RLock()
        self.remote_status = RemoteStatus()
        self.commander = Commander()
        self.octoprint_settings_updater = OctoPrintSettingsUpdater(self)
        self.jpeg_poster = JpegPoster(self)
        self.file_downloader = FileDownloader(self, _print_event_tracker)
        self.webcam_streamer = None
        self.linked_printer = DEFAULT_LINKED_PRINTER
        self.local_tunnel = None
        self.janus = JanusConn(self)
        self.client_conn = ClientConn(self)

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

        self.boost_status_update()

        try:
            if event == 'FirmwareData':
                self.octoprint_settings_updater.update_firmware(payload)
                self.post_update_to_server()
            elif event == 'SettingsUpdated':
                self.octoprint_settings_updater.update_settings()
                self.post_update_to_server()
            elif event.startswith("Print"):
                event_payload = _print_event_tracker.on_event(self, event, payload)
                if event_payload:
                    self.post_update_to_server(data=event_payload)
        except Exception as e:
            self.sentry.captureException(tags=get_tags())
    # ~~Shutdown Plugin

    def on_shutdown(self):
        if self.ss is not None:
            self.ss.close()
        if self.janus:
            self.janus.shutdown()
        if self.webcam_streamer:
            self.webcam_streamer.restore()
        if self.client_conn:
            self.client_conn.close()

        not_using_pi_camera()

    # ~~Startup Plugin

    def on_startup(self, host, port):
        self.octoprint_port = port if port else self._settings.getInt(["server", "port"])

    def on_after_startup(self):
        not_using_pi_camera()

        main_thread = threading.Thread(target=self.main_loop)
        main_thread.daemon = True
        main_thread.start()

    # Private methods

    def auth_headers(self, auth_token=None):
        return {"Authorization": "Token " + self.auth_token(auth_token)}

    def main_loop(self):
        global _print_event_tracker

        get_tags()  # init tags to minimize risk of race condition

        pdiscovery = None
        if not self.is_configured():
            pdiscovery = PrinterDiscovery(plugin=self)
            pdiscovery.start()

        self.linked_printer = self.wait_for_auth_token().get('printer', DEFAULT_LINKED_PRINTER)

        self.sentry.user_context({'id': self.auth_token()})
        _logger.info('Linked printer: {}'.format(self.linked_printer))
        _logger.debug('Plugin settings: {}'.format(self._settings.get_all_data()))

        # Notify plugin UI about the server connection status change
        self._plugin_manager.send_plugin_message(self._identifier, {'plugin_updated': True})

        # Janus may take a while to start, or fail to start. Put it in thread to make sure it does not block
        janus_thread = threading.Thread(target=self.janus.start)
        janus_thread.daemon = True
        janus_thread.start()

        if self.linked_printer.get('is_pro') and not self._settings.get(["disable_video_streaming"]):
            _logger.info('Starting webcam streamer')
            self.webcam_streamer = WebcamStreamer(self, self.sentry)
            stream_thread = threading.Thread(target=self.webcam_streamer.video_pipeline)
            stream_thread.daemon = True
            stream_thread.start()

        url = 'http://{}:{}'.format('127.0.0.1', self.octoprint_port)
        self.local_tunnel = LocalTunnel(
            base_url=url,
            on_http_response=self.send_ws_msg_to_server,
            on_ws_message=self.send_ws_msg_to_server,
            data_dir=self.get_plugin_data_folder(),
            sentry=self.sentry)

        jpeg_post_thread = threading.Thread(target=self.jpeg_poster.jpeg_post_loop)
        jpeg_post_thread.daemon = True
        jpeg_post_thread.start()

        status_update_to_client_thread = threading.Thread(target=self.status_update_to_client_loop)
        status_update_to_client_thread.daemon = True
        status_update_to_client_thread.start()

        message_to_server_thread = threading.Thread(target=self.message_to_server_loop)
        message_to_server_thread.daemon = True
        message_to_server_thread.start()

        while True:
            try:
                interval_in_seconds = POST_STATUS_INTERVAL_SECONDS
                if self.status_update_booster > 0:
                    interval_in_seconds /= 5

                if self.status_posted_to_server_ts < time.time() - interval_in_seconds:
                    self.post_update_to_server()

                time.sleep(1)
            except Exception as e:
                self.sentry.captureException(tags=get_tags())

    def message_to_server_loop(self):

        def on_server_ws_close(ws):
            if self.ss and self.ss.ws and self.ss.ws == ws:
                self._plugin_manager.send_plugin_message(self._identifier, {'plugin_updated': True})
                self.local_tunnel.close_all_octoprint_ws()
                self.ss = None

        def on_server_ws_open(ws):
            if self.ss and self.ss.ws and self.ss.ws == ws:
                self._plugin_manager.send_plugin_message(self._identifier, {'plugin_updated': True})

        server_ws_backoff = ExpoBackoff(300)
        while True:
            try:
                (data, as_binary) = self.message_queue_to_server.get()

                if not self.is_configured():
                    _logger.warning("Plugin not configured. Not sending message to server...")
                    continue

                if not self.linked_printer.get('id'):  # id is present only when auth_token is validated by the server
                    _logger.warning("auth_token is not validated. Not sending message to server...")
                    continue

                error_stats.attempt('server')

                if not self.ss or not self.ss.connected():
                    self.ss = WebSocketClient(self.canonical_ws_prefix() + "/ws/dev/", token=self.auth_token(), on_ws_msg=self.process_server_msg, on_ws_close=on_server_ws_close, on_ws_open=on_server_ws_open)

                if as_binary:
                    raw = bson.dumps(data)
                    _logger.debug("Sending binary ({} bytes) to server".format(len(raw)))
                else:
                    _logger.debug("Sending to server: \n{}".format(data))
                    if __python_version__ == 3:
                        raw = json.dumps(data, default=str)
                    else:
                        raw = json.dumps(data, encoding='iso-8859-1', default=str)
                self.ss.send(raw, as_binary=as_binary)
                server_ws_backoff.reset()
            except WebSocketConnectionException as e:
                _logger.warning(e)
                error_stats.add_connection_error('server', self)
                server_ws_backoff.more(e)
            except Exception as e:
                self.sentry.captureException(tags=get_tags())
                error_stats.add_connection_error('server', self)
                server_ws_backoff.more(e)

    def post_update_to_server(self, data=None):
        if not data:
            data = _print_event_tracker.octoprint_data(self)
        self.send_ws_msg_to_server(data)
        self.status_posted_to_server_ts = time.time()

    def send_ws_msg_to_server(self, data, as_binary=False):
        try:
            self.message_queue_to_server.put_nowait((data, as_binary))
        except queue.Full:
            _logger.warning("Server message queue is full, msg dropped")

    def process_server_msg(self, ws, raw_data):
        global _print_event_tracker
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

            need_status_boost = False
            for command in msg.get('commands', []):
                if command["cmd"] == "pause":
                    self.commander.prepare_to_pause(
                        self._printer,
                        self._printer_profile_manager.get_current(),
                        **command.get('args'))
                    self._printer.pause_print()

                if command["cmd"] == 'cancel':
                    self._printer.cancel_print()

                if command["cmd"] == 'resume':
                    self._printer.resume_print()

                if command["cmd"] == 'print':
                    self.start_print(**command.get('args'))

            if msg.get('passthru'):
                self.client_conn.on_message_to_plugin(msg.get('passthru'))
                need_status_boost = True

            if msg.get('janus') and self.janus:
                self.janus.pass_to_janus(msg.get('janus'))

            if msg.get('remote_status'):
                self.remote_status.update(msg.get('remote_status'))
                if self.remote_status['viewing']:
                    self.jpeg_poster.post_jpeg_if_needed(force=True)
                need_status_boost = True

            if msg.get('http.tunnel') and self.local_tunnel:
                tunnel_thread = threading.Thread(target=self.local_tunnel.send_http_to_local, kwargs=msg.get('http.tunnel'))
                tunnel_thread.is_daemon = True
                tunnel_thread.start()

            if msg.get('ws.tunnel') and self.local_tunnel:
                kwargs = msg.get('ws.tunnel')
                kwargs['type_'] = kwargs.pop('type')
                self.local_tunnel.send_ws_to_local(**kwargs)

            if need_status_boost:
                self.boost_status_update()
        except:
            self.sentry.captureException(tags=get_tags())

    def status_update_to_client_loop(self):
        while True:
            interval = 0.75 if self.status_update_booster > 0 else 2
            time.sleep(interval)
            self.post_printer_status_to_client()
            with self.status_update_lock:
                self.status_update_booster -= 1

    def post_printer_status_to_client(self):
        self.client_conn.send_msg_to_client(_print_event_tracker.octoprint_data(self, status_only=True))

    def boost_status_update(self):
        self.post_printer_status_to_client()
        with self.status_update_lock:
            self.status_update_booster = 20

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
            return None  # Triggers a backoff


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
