/*
 * View model for TheSpaghettiDetective
 *
 * Author: The Spaghetti Detective
 * License: AGPLv3
 */
$(function() {

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
            function(apiStatus) {
                var statusDiv = container.parent().find(".std-api-status");
                statusDiv.text(apiStatus.text);
                statusDiv.removeClass("text-success").removeClass("text-error");
                statusDiv.addClass(
                    apiStatus.succeeded ? "text-success" : "text-error"
                );
            }
        );
    }

    $("input.custom-server").change(function(e) {
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
    $("input.auth-token-input").keyup(function(e) {
        var container = $(this).parent();
        var token = $(this).val();
        clearTimeout(authTokenInputTimeout);
        authTokenInputTimeout = setTimeout(function() {
            testAuthToken(token, container);
        }, 500);
    });

    $("button.test-auth-token").click(function(event) {
        var container = $(this).parent();
        var token = $(this)
            .parent()
            .find("input.auth-token-input")
            .val();
        testAuthToken(token, container);
    });

    ko.bindingHandlers.showTrackerModal = {
        update: function(element, valueAccessor) {
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

        self.connectionErrors = { server: [], webcam: [] };
        self.hasShownServerError = false;
        self.hasShownWebcamError = false;

        self.streaming = ko.mapping.fromJS({eligible: false, piCamPresent: false});
        self.piCamResolutionOptions = [{id: "low", text: "Low"}, {id: "medium", text: "Medium"}, {id: "high", text: "High"}];

        self.onSettingsShown = function(plugin, data) {
            apiCommand({
                    command: "streaming"
                },
                function(data) {
                    ko.mapping.fromJS(data, self.streaming);
                }
            );
        }

        self.onDataUpdaterPluginMessage = function(plugin, data) {
            if (plugin != "thespaghettidetective") {
                return;
            }

            var text = "Unkonwn errors.";

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
                    'The Spaghetti Detective failed to connect to webcam. Please go to "Settings" -> "Webcam & Timelapse" and make sure the stream URL and snapshot URL are set correctly.';
            }

            new PNotify({
                title: "The Spaghetti Detective",
                text: text,
                type: "error",
                hide: false,
                confirm: {
                    confirm: true,
                    buttons: [
                        {
                            text: "Error Details",
                            click: function(notice) {
                                self.showTrackerModal();
                                notice.remove();
                            }
                        },
                        {
                            text: "Got It!",
                            click: function(notice) {
                                notice.remove();
                            }
                        },
                        {
                            text: "Close",
                            addClass: "remove_button"
                        }
                    ]
                },
                history: {
                    history: false
                },
                before_open: function(notice) {
                    notice
                        .get()
                        .find(".remove_button")
                        .remove();
                }
            });
        };

        self.showTrackerModal = function() {
            $.ajax("/api/plugin/thespaghettidetective", {
                method: "POST",
                contentType: "application/json",
                data: JSON.stringify({
                    command: "get_connection_errors"
                }),
                success: function(connectionErrors) {
                    for (var k in connectionErrors) {
                        var occurences = [];
                        for (var i in connectionErrors[k]) {
                            occurences.push(new Date(connectionErrors[k][i]));
                        }
                        self.connectionErrors[k] = occurences;
                    }
                    showMessageDialog({
                        title: "The Spaghetti Detective Diagnostic Report",
                        message: trackerModalBody()
                    });
                }
            });
        };

        self.openErrorTrackerModal = function() {
            self.showTrackerModal();
        };

        function trackerModalBody() {
            var errorBody =
                "<b>This window is to diagnose connection problems with The Spaghetti Detecitive server. It is not a diagnosis for your print failures.</b>";

            if (
                self.connectionErrors.server.length +
                    self.connectionErrors.webcam.length ==
                0
            ) {
                errorBody +=
                    '<p class="text-success">There have been no connection errors since OctoPrint rebooted.</p>';
            } else {
                errorBody +=
                    '<p class="text-error">The Spaghetti Detective plugin has run into issues. These issues may have prevented The Detective from watching your print effectively. Please check out our <a href="https://www.thespaghettidetective.com/docs/no-pics/">trouble-shooting page</a> or <a href="https://www.thespaghettidetective.com/docs/support/">reach out to us</a> for help.</p>';
            }

            if (self.connectionErrors.server.length > 0) {
                errorBody += '<hr /><p class="text-error">The Spaghetti Detective failed to connect to the server <b>' + self.connectionErrors.server.length + '</b> times since OctoPrint rebooted.</p>';
                errorBody += '<ul><li>The first error occurred at: <b>' + self.connectionErrors.server[0] + '</b>.</li>';
                errorBody += '<li>The most recent error occurred at: <b>' + self.connectionErrors.server[self.connectionErrors.server.length-1] + '</b>.</li></ul>';
                errorBody += '<p>Please check your OctoPrint\'s internet connection to make sure it has reliable connection to the internet.<p>';
            }

            if (self.connectionErrors.webcam.length > 0) {
                errorBody += '<hr /><p class="text-error">The Spaghetti Detective failed to connect to the webcam <b>' + self.connectionErrors.webcam.length + '</b> times since OctoPrint rebooted.</p>';
                errorBody += '<ul><li>The first error occurred at: <b>' + self.connectionErrors.webcam[0] + '</b>.</li>';
                errorBody += '<li>The most recent error occurred at: <b>' + self.connectionErrors.webcam[self.connectionErrors.webcam.length-1] + '</b>.</li></ul>';
                errorBody += "<p>Please go to \"Settings\" -> \"Webcam & Timelapse\" and make sure the stream URL and snapshot URL are set correctly. Also make sure these URLs can be accessed from within the OctoPrint (not just from your browser).</p>";
            }
            return errorBody;
        }
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
