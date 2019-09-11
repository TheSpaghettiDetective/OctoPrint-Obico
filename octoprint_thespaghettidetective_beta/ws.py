# coding=utf-8

import time
import websocket

class WebSocketClientException(Exception):
    pass

class WebSocketClient:

    def __init__(self, url, token=None, on_ws_msg=None, on_ws_close=None, on_ws_error=None, subprotocols=None):
        #websocket.enableTrace(True)

        def on_error(ws, error):
            if on_ws_error:
                on_ws_error(ws, error)

        def on_message(ws, msg):
            if on_ws_msg:
                on_ws_msg(ws, msg)

        def on_close(ws):
            if on_ws_close:
                on_ws_close(ws)

        header = ["authorization: bearer " + token] if token else None
        self.ws = websocket.WebSocketApp(url,
                                  on_message = on_message,
                                  on_close = on_close,
                                  on_error = on_error,
                                  header = header,
                                  subprotocols=subprotocols
        )

    def run(self):
        self.ws.run_forever()

    def send_text(self, data):
        if self.connected():
            self.ws.send(data)

    def connected(self):
        return self.ws.sock and self.ws.sock.connected

    def disconnect(self):
        self.ws.keep_running = False;
        self.ws.close()
