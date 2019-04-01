/*
 * View model for TheSpaghettiDetective
 *
 * Author: The Spaghetti Detective
 * License: AGPLv3
 */
$(function() {
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

    function ThespaghettidetectiveBetaViewModel(parameters) {
        var self = this;
        // assign the injected parameters, e.g.:
        // self.loginStateViewModel = parameters[0];
        self.settingsViewModel = parameters[0];

        // TODO: Implement your plugin's view model here.
    }

    /* view model class, parameters for constructor, container to bind to
     * Please see http://docs.octoprint.org/en/master/plugins/viewmodels.html#registering-custom-viewmodels for more details
     * and a full list of the available options.
     */
    OCTOPRINT_VIEWMODELS.push({
        construct: ThespaghettidetectiveBetaViewModel,
        // ViewModels your plugin depends on, e.g. loginStateViewModel, settingsViewModel, ...
        dependencies: [ "settingsViewModel" ],
        // Elements to bind to, e.g. #settings_plugin_thespaghettidetective, #tab_plugin_thespaghettidetective, ...
        elements: [ '#wizard_plugin_thespaghettidetective_beta', '#settings_plugin_thespaghettidetective_beta' ]
    });
});
