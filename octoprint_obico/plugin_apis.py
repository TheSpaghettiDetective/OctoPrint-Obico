import flask
import logging

from .utils import server_request
from .lib.error_stats import error_stats
from .lib import alert_queue

_logger = logging.getLogger('octoprint.plugins.obico')

def get_api_commands():
    return dict(
        verify_code=['code', 'endpoint_prefix'],
        get_plugin_status=[],
        toggle_sentry_opt=[],
        test_server_connection=[],
        update_printer=['name'],
    )


def verify_code(plugin, data):
    configured_auth_token = plugin._settings.get(["auth_token"])
    resp = server_request('POST', '/api/v1/octo/verify/?code=' + data["code"], plugin)
    succeeded = resp.ok if resp is not None else None
    printer = None
    if succeeded:
        printer = resp.json()['printer']
        plugin._settings.set(["auth_token"], printer['auth_token'], force=True)
        plugin._settings.save(force=True)
        if configured_auth_token:
            alert_queue.add_alert({
                'level': 'warning',
                'cause': 'restart_required',
                'text': 'Settings saved! If you are in the setup wizard, restart OctoPrint after the setup is done. Otherwise, restart OctoPrint now for the changes to take effect.',
                'buttons': ['never', 'ok']
            }, plugin)

    return {'succeeded': succeeded, 'printer': printer}


def on_api_command(plugin, command, data):
    _logger.debug('API called: {}'.format(command))
    try:
        if command == "verify_code":
            plugin._settings.set(["endpoint_prefix"], data["endpoint_prefix"], force=True)
            return flask.jsonify(verify_code(plugin, data))

        if command == "get_plugin_status":
            webcam_streamer = plugin.janus and plugin.janus.webcam_streamer
            results = dict(
                server_status=dict(
                    is_connected=plugin.ss and plugin.ss.connected(),
                    status_posted_to_server_ts=plugin.status_posted_to_server_ts,
                    bailed_because_tsd_plugin_running=plugin.bailed_because_tsd_plugin_running,
                ),
                linked_printer=plugin.linked_printer,
                streaming_status=dict(
                    is_pi_camera=webcam_streamer and bool(webcam_streamer.pi_camera),
                    webrtc_streaming=webcam_streamer and not webcam_streamer.shutting_down,
                    compat_streaming=webcam_streamer and webcam_streamer.compat_streaming),
                    error_stats=error_stats.as_dict(),
                    alerts=alert_queue.fetch_and_clear(),
                )
            if plugin._settings.get(["auth_token"]):     # Ask to opt in sentry only after wizard is done.
                sentry_opt = plugin._settings.get(["sentry_opt"])
                if sentry_opt == 'out':
                    plugin._settings.set(["sentry_opt"], 'asked')
                    plugin._settings.save(force=True)
                results['sentry_opt'] = sentry_opt

            return flask.jsonify(results)

        if command == "toggle_sentry_opt":
            plugin._settings.set(["sentry_opt"], 'out' if plugin._settings.get(["sentry_opt"]) == 'in' else 'in', force=True)
            plugin._settings.save(force=True)

        if command == "test_server_connection":
            resp = plugin.tsd_api_status()
            return flask.jsonify({'status_code': resp.status_code if resp is not None else None})

        if command == "update_printer":
            resp = server_request('PATCH', '/api/v1/octo/printer/', plugin, headers=plugin.auth_headers(), json=dict(name=data['name']))
            if resp:
                return flask.jsonify({'succeeded': resp.ok, 'printer': resp.json().get('printer')})
            else:
                return flask.jsonify({'succeeded': False})

    except Exception as e:
        plugin.sentry.captureException()
        raise
