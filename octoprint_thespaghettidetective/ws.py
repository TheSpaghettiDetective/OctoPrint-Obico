# coding=utf-8

import time
import websocket
import logging
import threading

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

class WebSocketConnectionException(Exception):
    pass

class WebSocketClient:

    def __init__(self, url, token=None, on_ws_msg=None, on_ws_close=None, on_ws_open=None, subprotocols=None, wait_secs=120):
        self._mutex = threading.RLock()

        def on_error(ws, error):
            _logger.warning('Server WS ERROR: {}'.format(error))
            self.close()

        def on_message(ws, msg):
            if on_ws_msg:
                on_ws_msg(ws, msg)

        def on_close(ws):
            _logger.debug('WS Closed')
            if on_ws_close:
                on_ws_close(ws)

        def on_open(ws):
            _logger.debug('WS Opened')
            if on_ws_open:
                on_ws_open(ws)

        _logger.debug('Connecting to websocket: {}'.format(url))
        header = ["authorization: bearer " + token] if token else None
        self.ws = websocket.WebSocketApp(url,
                                  on_message = on_message,
                                  on_open = on_open,
                                  on_close = on_close,
                                  on_error = on_error,
                                  header = header,
                                  subprotocols=subprotocols
        )
        wst = threading.Thread(target=self.ws.run_forever)
        wst.daemon = True
        wst.start()

        for i in range(wait_secs * 10):  # Give it up to 120s for ws hand-shaking to finish
            if self.connected():
                return
            time.sleep(0.1)
        self.ws.close()
        raise WebSocketConnectionException('Not connected to websocket server after {}s'.format(wait_secs))

    def send(self, data, as_binary=False):
        with self._mutex:
            if self.connected():
                if as_binary:
                    self.ws.send(data, opcode=websocket.ABNF.OPCODE_BINARY)
                else:
                    self.ws.send(data)

    def connected(self):
        with self._mutex:
            return self.ws.sock and self.ws.sock.connected

    def close(self):
        with self._mutex:
            self.ws.keep_running = False
            self.ws.close()

if __name__ == "__main__":
    import yaml
    import sys

    def on_msg(ws, msg):
        print(msg)

    def on_close(ws):
        print('Closed')

    with open(sys.argv[1]) as stream:
        config = yaml.load(stream.read()).get('plugins', {}).get('thespaghettidetective', {})

    url = config.get('endpoint_prefix', 'https://app.thespaghettidetective.com').replace('http', 'ws') + '/ws/dev/'
    token = config.get('auth_token')
    print('Connecting to:\n{}\nwith token:\n{}\n'.format(url, token))
    websocket.enableTrace(True)
    ws = WebSocketClient(url, token=token, on_ws_msg=on_msg, on_ws_close=on_close)
    time.sleep(1)
    ws.close()
    time.sleep(1)
