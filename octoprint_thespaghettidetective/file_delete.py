import logging
import requests
import os
import sys
import time
import threading
import octoprint.server
from octoprint.filemanager.util import AbstractFileWrapper
import io
from .utils import get_tags

_logger = logging.getLogger('octoprint.plugins.thespaghettidetective')
UPLOAD_FOLDER = 'TheSpaghettiDetectiveUpload'


class RequestFileWrapper(AbstractFileWrapper):

    def __init__(self, filename, req):
        AbstractFileWrapper.__init__(self, filename)
        self.req = req

    def save(self, path, permissions=None):
        with open(path, 'wb') as f:
            f.write(self.req.content)

        if permissions is not None:
            os.chmod(path, permissions)

    def stream(self):
        return io.StringIO(self.req.content)


class FileDeleter:

    def __init__(self, plugin, _print_event_tracker):
        self.plugin = plugin
        self._print_event_tracker = _print_event_tracker

    def delete_file(self, gcode_file):
        try:
            _logger.warning(
                'Received delete command for {} '.format(gcode_file))

            filename = gcode_file['safe_filename']
            target_path, safe_filename, filename = self._get_unique_path_and_filename(
                filename)

            """
            # For reference
            file_object = RequestFileWrapper(filename, r)
            octoprint.server.fileManager.add_file(
                'local',
                target_path,
                file_object,
                links=None,
                allow_overwrite=False,
                display=filename,
            )
            """

            """
            if self._print_event_tracker.get_tsd_gcode_file_id() or self.plugin._printer.get_current_data().get('state', {}).get('text') != 'Operational':
                return {'error': 'Currently downloading or printing!'}

            self._print_event_tracker.set_tsd_gcode_file_id(gcode_file['id'])

            self.__ensure_storage__()

            filename = gcode_file['safe_filename']
            target_path, safe_filename, filename = self._get_unique_path_and_filename(
                filename)

            print_thread = threading.Thread(target=self.__download_and_print__, args=(
                gcode_file, target_path, filename))
            print_thread.daemon = True
            print_thread.start()

            return {'target_path': target_path}
            """

        except Exception as e:
            self.plugin.sentry.captureException(tags=get_tags())

    
