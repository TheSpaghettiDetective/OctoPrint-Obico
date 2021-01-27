import socket
import logging

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')


class UDPClient(object):

    def __init__(self, addr, port, q):
        self.addr = addr
        self.port = port
        self.q = q

    def run(self):
        sock = None
        while True:
            payload = self.q.get()
            if payload is None:
                if sock:
                    try:
                        sock.close()
                    except socket.error as ex:
                        _logger.debug('could not close udp socket (%s)' % ex)
                    return

            if sock is None:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                except OSError as ex:
                    _logger.debug('could not open udp socket (%s)' % ex)

            if sock is not None:
                try:
                    sock.sendto(payload, (self.addr, self.port))
                except socket.error as ex:
                    _logger.debug(
                        'could not send to janus datachannel (%s)' % ex)
                except OSError as ex:
                    _logger.debug('udp socket might be closed (%s)' % ex)
                    sock = None

    def close(self):
        self.q.put(None)
