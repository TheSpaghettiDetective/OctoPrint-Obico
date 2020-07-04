/*
 * View model for TheSpaghettiDetective
 *
 * Author: The Spaghetti Detective
 * License: AGPLv3
 */
$(function () {

    function apiCommand(data, success) {
        $.ajax("/api/plugin/thespaghettidetective", {
            method: "POST",
            contentType: "application/json",
            data: JSON.stringify(data),
            success: success,
        });
    }

    function testAuthToken(token, container) {
        apiCommand({
            command: "test_auth_token",
            auth_token: container.find("input.auth-token-input").val()
        },
            function (apiStatus) {
                var statusDiv = container.parent().find(".std-api-status");
                statusDiv.text(apiStatus.text);
                statusDiv.removeClass("text-success").removeClass("text-error");
                statusDiv.addClass(
                    apiStatus.succeeded ? "text-success" : "text-error"
                );
            }
        );
    }

    $("input.custom-server").change(function (e) {
        var container = $(this)
            .parent()
            .parent();
        if ($(this).is(":checked")) {
            container.find("input.endpoint-prefix").prop("disabled", false);
        } else {
            container.find("input.endpoint-prefix").prop("disabled", true);
        }
    });

    var authTokenInputTimeout = null;
    $("input.auth-token-input").keyup(function (e) {
        var container = $(this).parent();
        var token = $(this).val();
        clearTimeout(authTokenInputTimeout);
        authTokenInputTimeout = setTimeout(function () {
            testAuthToken(token, container);
        }, 500);
    });

    $("button.test-auth-token").click(function (event) {
        var container = $(this).parent();
        var token = $(this)
            .parent()
            .find("input.auth-token-input")
            .val();
        testAuthToken(token, container);
    });

    ko.bindingHandlers.showTrackerModal = {
        update: function (element, valueAccessor) {
            var value = valueAccessor();
            if (ko.utils.unwrapObservable(value)) {
                $(element).modal("show");
                // this is to focus input field inside dialog
            } else {
                $(element).modal("hide");
            }
        }
    };

    function ThespaghettidetectiveViewModel(parameters) {
        var self = this;

        // assign the injected parameters, e.g.:
        // self.loginStateViewModel = parameters[0];
        self.settingsViewModel = parameters[0];

        self.errorStats = {};
        self.hasShownServerError = false;
        self.hasShownWebcamError = false;
        self.streaming = ko.mapping.fromJS({ is_pro: false, is_pi_camera: false });
        self.piCamResolutionOptions = [{ id: "low", text: "Low" }, { id: "medium", text: "Medium" }, { id: "high", text: "High" }, { id: "ultra_high", text: "Ultra High" }];
        self.sentryOptedIn = ko.pureComputed(function () {
            return self.settingsViewModel.settings.plugins.thespaghettidetective.sentry_opt() === "in";
        }, self);

        self.onSettingsShown = function (plugin, data) {
            apiCommand({
                command: "get_plugin_status"
            },
                function (data) {
                    ko.mapping.fromJS(data.streaming_status, self.streaming);
                }
            );
        }

        self.onStartupComplete = function (plugin, data) {
            apiCommand({
                command: "get_sentry_opt",
            }, function (data) {
                if (data.sentryOpt === "out") {
                    var sentrynotice = new PNotify({
                        title: "The Spaghetti Detective",
                        text: "<p>Turn on bug reporting to help us make TSD plugin better?</p><p>The debugging info included in the report will be anonymized.</p>",
                        hide: false,
                        destroy: true,
                        confirm: {
                            confirm: true,
                        },
                    });
                    sentrynotice.get().on('pnotify.confirm', function () {
                        self.toggleSentryOpt();
                    });
                }
            });
        }

        self.onDataUpdaterPluginMessage = function (plugin, data) {
            if (plugin != "thespaghettidetective") {
                return;
            }

            var text = null;
            var msgType = "error";
            var buttons = [
                {
                    text: "Close",
                    addClass: "remove_button"
                },
            ]

            if (data.new_error) {
                msgType = "error";
                buttons = buttons.concat([
                    {
                        text: "Error Details",
                        click: function (notice) {
                            self.showTrackerModal();
                            notice.remove();
                        }
                    },
                    {
                        text: "Got It!",
                        click: function (notice) {
                            notice.remove();
                        }
                    }
                ]);
                if (data.new_error == "server") {
                    if (self.hasShownServerError) {
                        return;
                    }
                    self.hasShownServerError = true;
                    text =
                        "The Spaghetti Detective failed to connect to the server. Please make sure OctoPrint has a reliable internet connection.";
                } else if (data.new_error == "webcam") {
                    if (self.hasShownWebcamError) {
                        return;
                    }
                    self.hasShownWebcamError = true;
                    text =
                        'The Spaghetti Detective plugin failed to connect to the webcam. Please go to "Settings" -> "Webcam & Timelapse" and make sure the stream URL and snapshot URL are set correctly. Or follow <a href="https://www.thespaghettidetective.com/docs/webcam-connection-error-popup">this trouble-shooting guide</a>.';
                }
            }
            if (_.get(data, 'new_warning', '') == 'streaming') {
                var streamingWarningAcked = localStorage.getItem("tsd.streamingWarningAcked");
                if (!streamingWarningAcked) {
                    msgType = "notice";
                    text =
                        '<p>Premium webcam streaming failed to start. The Spaghetti Detective has switched to basic streaming.</p><p><a href="https://www.thespaghettidetective.com/docs/webcam-switched-to-basic-streaming/">Learn more >>></a></p>';
                    buttons = buttons.concat([
                        {
                            text: "Ignore",
                            click: function (notice) {
                                localStorage.setItem("tsd.streamingWarningAcked", true);
                                notice.remove();
                            }
                        },
                    ]);
                }
            }
            if (_.get(data, 'new_warning', '') == 'cpu') {
                msgType = "notice";
                text =
                    '<p>Premium streaming uses excessive CPU. This may negatively impact your print quality. Consider switch off "compatibility mode", or disable premium streaming. <a href="https://www.thespaghettidetective.com/docs/compatibility-mode-excessive-cpu">Learn more >>></a></p>';

            }

            if (text) {
                new PNotify({
                    title: "The Spaghetti Detective",
                    text: text,
                    type: msgType,
                    hide: false,
                    confirm: {
                        confirm: true,
                        buttons: buttons,
                    },
                    history: {
                        history: false
                    },
                    before_open: function (notice) {
                        notice
                            .get()
                            .find(".remove_button")
                            .remove();
                    }
                });
            }
        };

        self.showTrackerModal = function () {
            apiCommand({
                command: "get_plugin_status"
            },
                function (status) {
                    var stats = status.error_stats;
                    for (var k in stats) {
                        var errors = [];
                        for (var i in stats[k].errors) {
                            errors.push(new Date(stats[k].errors[i]));
                        }
                        self.errorStats[k] = { attempts: stats[k].attempts, errors: errors };
                    }
                    showMessageDialog({
                        title: "The Spaghetti Detective Diagnostic Report",
                        message: trackerModalBody()
                    });
                });
        };

        self.openErrorTrackerModal = function () {
            self.showTrackerModal();
        };

        function trackerModalBody() {
            var serverErrors = _.get(self.errorStats, 'server.errors', []);
            var webcamErrors = _.get(self.errorStats, 'webcam.errors', []);
            var errorBody = '<b>This window is to diagnose connection problems with The Spaghetti Detective server. It is not a diagnosis for your print failures.</b>';
            if (serverErrors.length + webcamErrors.length == 0) {
                errorBody +=
                    '<p class="text-success">There have been no connection errors since OctoPrint rebooted.</p>';
            } else {
                errorBody +=
                    '<p class="text-error">The Spaghetti Detective plugin has run into issues. These issues may have prevented The Detective from watching your print effectively. Please check out our <a href="https://www.thespaghettidetective.com/docs/connectivity-error-report/">trouble-shooting page</a> or <a href="https://www.thespaghettidetective.com/docs/contact-us-for-support/">reach out to us</a> for help.</p>';
            }

            if (serverErrors.length > 0) {
                errorBody += '<hr /><p class="text-error">The plugin has failed to connect to the server <b>' + serverErrors.length + '</b> times (error rate <b>' + Math.round(serverErrors.length / self.errorStats.server.attempts * 100) + '%</b>) since OctoPrint rebooted.</p>';
                errorBody += '<ul><li>The first error occurred at: <b>' + serverErrors[0] + '</b>.</li>';
                errorBody += '<li>The most recent error occurred at: <b>' + serverErrors[serverErrors.length - 1] + '</b>.</li></ul>';
                errorBody += '<p>Please check your OctoPrint\'s internet connection to make sure it has reliable connection to the internet.<p>';
            }

            if (webcamErrors.length > 0) {
                errorBody += '<hr /><p class="text-error">The plugin has failed to connect to the webcam <b>' + webcamErrors.length + '</b> times (error rate <b>' + Math.round(webcamErrors.length / self.errorStats.webcam.attempts * 100) + '%</b>) since OctoPrint rebooted.</p>';
                errorBody += '<ul><li>The first error occurred at: <b>' + webcamErrors[0] + '</b>.</li>';
                errorBody += '<li>The most recent error occurred at: <b>' + webcamErrors[webcamErrors.length - 1] + '</b>.</li></ul>';
                errorBody += "<p>Please go to \"Settings\" -> \"Webcam & Timelapse\" and make sure the stream URL and snapshot URL are set correctly.</p>";
            }
            return errorBody;
        }

        self.toggleSentryOpt = function (ev) {
            apiCommand({
                command: "toggle_sentry_opt",
            });
            return true;
        };
    }

    /* view model class, parameters for constructor, container to bind to
     * Please see http://docs.octoprint.org/en/master/plugins/viewmodels.html#registering-custom-viewmodels for more details
     * and a full list of the available options.
     */
    OCTOPRINT_VIEWMODELS.push({
        construct: ThespaghettidetectiveViewModel,
        // ViewModels your plugin depends on, e.g. loginStateViewModel, settingsViewModel, ...
        dependencies: ["settingsViewModel"],
        // Elements to bind to, e.g. #settings_plugin_thespaghettidetective, #tab_plugin_thespaghettidetective, ...
        elements: [
            "#wizard_plugin_thespaghettidetective",
            "#settings_plugin_thespaghettidetective"
        ]
    });
});
