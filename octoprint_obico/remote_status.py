# coding=utf-8
from __future__ import absolute_import
import threading

class RemoteStatus:

    def __init__(self):
        self._mutex = threading.RLock()
        self.__items__ = {"viewing": False, "should_watch": False}

    def __getitem__(self, key):
        with self._mutex:
            return self.__items__[key]

    def __setitem__(self, key, value):
        with self._mutex:
            self.__items__[key] = value

    def update(self, data):
        for key in ('viewing', 'should_watch'):
            if key in data:
                self[key] = data[key]

    def __str__(self):
        return str(self.__items__)