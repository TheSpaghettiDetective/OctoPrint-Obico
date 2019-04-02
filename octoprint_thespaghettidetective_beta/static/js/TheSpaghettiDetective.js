/*
 * View model for TheSpaghettiDetective
 *
 * Author: The Spaghetti Detective
 * License: AGPLv3
 */
$(function() {
    PNotify.prototype.options.confirm.buttons = [];

    function testAuthToken(token, container) {
        $.ajax("/api/plugin/thespaghettidetective_beta", {
            method: "POST",
            contentType: "application/json",
            data: JSON.stringify({
                command: "test_auth_token",
                auth_token: container.find("input.auth-token-input").val()
            }),
            success: function(apiStatus) {
                var statusDiv = container.parent().find(".std-api-status");
                statusDiv.text(apiStatus.text);
                statusDiv.removeClass("text-success").removeClass("text-error");
                statusDiv.addClass(
                    apiStatus.succeeded ? "text-success" : "text-error"
                );
            }
        });
    }

    $('input.custom-server').change( function(e) {
        var container = $(this).parent().parent();
        if($(this).is(':checked')) {
            container.find('input.endpoint-prefix').prop('disabled', false);
        } else {
            container.find('input.endpoint-prefix').prop('disabled', true);
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
        var token = $(this).parent().find('input.auth-token-input').val();
        testAuthToken(token, container);
    });

    ko.bindingHandlers.showTrackerModal = {
        update: function (element, valueAccessor) {
            var value = valueAccessor();
            if (ko.utils.unwrapObservable(value)) {
                $(element).modal('show');
                // this is to focus input field inside dialog
            }
            else {
                $(element).modal('hide');
            }
        }
    };

    function ThespaghettidetectiveBetaErrorTrackerViewModel(parameters) {
        var self = this;

        self.connectionErrors = ko.observable({server: [], webcam: []});
        self.hasShownServerError = false;
        self.hasShownWebcamError = false;
        self.trackerModalVisible = ko.observable(false);

        self.onDataUpdaterPluginMessage = function(plugin, data) {
            if (plugin != "thespaghettidetective_beta") {
                return;
            }

            var text = "Unkonwn errors.";

            if (data.new_error == "server") {

                if (self.hasShownServerError) {
                    return;
                }
                self.hasShownServerError = true;
                text = "The Spaghetti Detective failed to connect to the server. Please make sure OctoPrint has a reliable internet connection."

            } else if (data.new_error == "webcam") {

                if (self.hasShownWebcamError) {
                    return;
                }
                self.hasShownWebcamError = true;
                text = "The Spaghetti Detective failed to connect to webcam. Please go to \"Settings\" -> \"Webcam & Timelapse\" and make sure the stream URL and snapshot URL are set correctly."

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
                                notice.update({hide: true});
                            }
                        }
                    ]
                },
    			history: {
    			    history: false
    			},
            });
        };

        self.showTrackerModal = function() {
            self.trackerModalVisible(true);
            $.ajax("/api/plugin/thespaghettidetective_beta", {
                method: "POST",
                contentType: "application/json",
                data: JSON.stringify({
                    command: "get_connection_errors",
                }),
                success: function(connectionErrors) {
                    var errors = {};
                    for (var k in connectionErrors) {
                        var occurences = [];
                        for (var i in connectionErrors[k]) {
                            occurences.push(new Date(connectionErrors[k][i]));
                        }
                        errors[k] = occurences;
                    }
                    self.connectionErrors(errors);
                }
            });
        }
    }

    function ThespaghettidetectiveBetaViewModel(parameters) {
        var self = this;

        // assign the injected parameters, e.g.:
        // self.loginStateViewModel = parameters[0];
        self.settingsViewModel = parameters[0];
        self.errorTrackerViewModel = parameters[1];

        self.openErrorTrackerModal = function() {
            showMessageDialog({
                title: gettext("Stream test"),
                message: trackerModalBody(self.errorTrackerViewModel.connectionErrors),
			});
            //self.errorTrackerViewModel.showTrackerModal();
        }

		function trackerModalBody(connectionErrors) {
            var errorBody = '<p class="error">The Spaghetti Detective failed to connect to the server ' + connectionErrors.length + ' times since OctoPrint rebooted.</p>';
            return errorBody;
		}
    }

    /* view model class, parameters for constructor, container to bind to
     * Please see http://docs.octoprint.org/en/master/plugins/viewmodels.html#registering-custom-viewmodels for more details
     * and a full list of the available options.
     */
    OCTOPRINT_VIEWMODELS.push({
        construct: ThespaghettidetectiveBetaErrorTrackerViewModel,
        dependencies: [],
        elements: [ '#thespaghettidetective_error_tracker_modal']
    });
    OCTOPRINT_VIEWMODELS.push({
        construct: ThespaghettidetectiveBetaViewModel,
        // ViewModels your plugin depends on, e.g. loginStateViewModel, settingsViewModel, ...
        dependencies: [ "settingsViewModel", "thespaghettidetectiveBetaErrorTrackerViewModel" ],
        // Elements to bind to, e.g. #settings_plugin_thespaghettidetective, #tab_plugin_thespaghettidetective, ...
        elements: [ '#wizard_plugin_thespaghettidetective_beta', '#settings_plugin_thespaghettidetective_beta' ]
    });
});
