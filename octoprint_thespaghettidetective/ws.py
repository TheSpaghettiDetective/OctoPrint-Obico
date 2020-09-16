# coding=utf-8

import time
import websocket
import logging
import threading

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')

class WebSocketClientException(Exception):
    pass

class WebSocketClient:

    def __init__(self, url, token=None, on_ws_msg=None, on_ws_close=None, on_ws_error=None, subprotocols=None):
        self._mutex = threading.RLock()

        def on_error(ws, error):
            if on_ws_error:
                on_ws_error(ws, error)

        def on_message(ws, msg):
            if on_ws_msg:
                on_ws_msg(ws, msg)

        def on_close(ws):
            if on_ws_close:
                on_ws_close(ws)

        _logger.debug('Connecting to websocket: {}'.format(url))
        header = ["authorization: bearer " + token] if token else None
        self.ws = websocket.WebSocketApp(url,
                                  on_message = on_message,
                                  on_close = on_close,
                                  on_error = on_error,
                                  header = header,
                                  subprotocols=subprotocols
        )

        wst = threading.Thread(target=self.ws.run_forever)
        wst.daemon = True
        wst.start()

        for i in range(50):      # Wait for up to 5 seconds
            if self.connected():
                return
            time.sleep(0.1)
        self.ws.close()
        raise WebSocketClientException('Not connected to websocket server after 5s')


    def run(self):
        self.ws.run_forever()

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
