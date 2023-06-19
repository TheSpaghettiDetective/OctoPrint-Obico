import bson
import logging
import json
import socket
import threading
import time
import sys
import zlib
import re
from collections import deque

from .janus import JANUS_SERVER, JANUS_PRINTER_DATA_PORT, MAX_PAYLOAD_SIZE

__python_version__ = 3 if sys.version_info >= (3, 0) else 2

_logger = logging.getLogger('octoprint.plugins.obico')

class ClientConn:

    def __init__(self, plugin):
        self.plugin = plugin
        self.printer_data_channel_conn = DataChannelConn(JANUS_SERVER, JANUS_PRINTER_DATA_PORT)
        self.seen_refs = deque(maxlen=25)  # contains "last" 25 passthru refs
        self.seen_refs_lock = threading.RLock()

    def on_message_to_plugin(self, msg):
        target = getattr(self.plugin, msg.get('target'))
        func = getattr(target, msg['func'], None)
        if not func:
            self.plugin.sentry.captureMessage('Function "{} in target "{}" not found'.format(msg['func'], msg['target']))
            return

        ack_ref = msg.get('ref')
        if ack_ref is not None:
            # same msg may arrive through both ws and datachannel
            with self.seen_refs_lock:
                if ack_ref in self.seen_refs:
                    _logger.debug('Got duplicate ref, ignoring msg')
                    return
                # no need to remove item or check fullness
                # as deque manages that when maxlen is set
                self.seen_refs.append(ack_ref)

        error = None
        try:
            ret = func(*(self.extract_args(msg)), **(self.extract_kwargs(msg)))
        except Exception as e:
            error = str(e)
            self.plugin.sentry.captureException()

        if ack_ref:

            if error:
                resp = {'ref': ack_ref, 'error': error}
            else:
                resp = {'ref': ack_ref, 'ret': ret}

            self.plugin.send_ws_msg_to_server({'passthru': resp})
            self.send_msg_to_client(resp)

        self.plugin.boost_status_update()

    def send_msg_to_client(self, data):
        payload = json.dumps(data, default=str).encode('utf8')
        if __python_version__ == 3:
            compressor  = zlib.compressobj(
                level=zlib.Z_DEFAULT_COMPRESSION, method=zlib.DEFLATED,
                wbits=15, memLevel=8, strategy=zlib.Z_DEFAULT_STRATEGY)
        else:
            # no kw args
            compressor  = zlib.compressobj(
                zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, 15, 8, zlib.Z_DEFAULT_STRATEGY)

        compressed_data = compressor.compress(payload)
        compressed_data += compressor.flush()

        self.printer_data_channel_conn.send(compressed_data)

    def close(self):
        self.printer_data_channel_conn.close()

    def extract_args(self, msg):
        args = msg.get("args", [])
        if 'jog' == msg['func']:
            # invert the jogging if configured, since OctoPrint doesn't do it interally for us
            axes = self.plugin._printer_profile_manager.get_current_or_default().get('axes', {})
            for arg in args:
                axis = list(arg.keys())[0] # Each arg should be a dict with a single entry
                if axis in ['x', 'y', 'z'] and axes.get(axis, {}).get('inverted', False):
                    arg[axis] = -arg[axis]

        return args

    def extract_kwargs(self, msg):
        kwargs = msg.get("kwargs", {})
        if 'list_files' == msg['func'] and 'filter' in kwargs:

            def filter_by_name(keyword):
                return lambda x: re.search(keyword, x['name'], re.IGNORECASE)

            # filter is a lambda function in octoprint api
            kwargs['filter'] = filter_by_name(kwargs['filter'])

        return kwargs

class DataChannelConn(object):

    def __init__(self, addr, port):
        self.addr = addr
        self.port = port
        self.sock = None
        self.sock_lock = threading.RLock()

    def send(self, payload):
        if len(payload) > MAX_PAYLOAD_SIZE:
            _logger.error('datachannel payload too big (%s)' % (len(payload), ))
            return

        with self.sock_lock:
            if self.sock is None:
                try:
                    self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                except OSError as ex:
                    _logger.error('could not open udp socket (%s)' % ex)

            if self.sock is not None:
                try:
                    self.sock.sendto(payload, (self.addr, self.port))
                except socket.error as ex:
                    _logger.error(
                        'could not send to janus datachannel (%s)' % ex)
                except OSError as ex:
                    _logger.error('udp socket might be closed (%s)' % ex)
                    self.sock = None

    def close(self):
        with self.sock_lock:
            self.sock.close()
            self.sock = None
