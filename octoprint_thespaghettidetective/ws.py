# coding=utf-8

import time
import websocket

class ServerSocket:
    def on_error(self, ws, error):
        pass

    def __init__(self, url, token, on_message, on_close):
        #websocket.enableTrace(True)
        self.ws = websocket.WebSocketApp(url,
                                  on_message = on_message,
                                  on_close = on_close,
                                  on_error = self.on_error,
                                  header = ["authorization: bearer " + token],)
                                  #subprotocols=["binary", "base64"])

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
