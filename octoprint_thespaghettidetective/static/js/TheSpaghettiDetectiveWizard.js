/*
 * View model for TheSpaghettiDetective Wizard
 *
 * Author: The Spaghetti Detective
 * License: AGPLv3
 */
$(function () {

    

    function apiCommand(data, success, error) {
        $.ajax("api/plugin/thespaghettidetective", {
            method: "POST",
            contentType: "application/json",
            data: JSON.stringify(data),
            success: success,
            error: error,
        });
    }

    function ThespaghettidetectiveWizardViewModel(parameters) {
        var self = this;

        // assign the injected parameters, e.g.:
        // self.loginStateViewModel = parameters[0];
        self.settingsViewModel = parameters[0];

        self.step = ko.observable(1);
        self.securityCode = ko.observable('');
        self.verifying = ko.observable(false);
        self.userAgreementChecked = ko.observable(true);

        self.nextStep = function() {
            self.step(self.step() + 1);
        };

        self.toStep = function(step) {
            self.step(step);
        };

        self.prevStep = function() {
            self.step(self.step() - 1);
        };

        self.toggleCheck = function() {
            self.userAgreementChecked(!self.userAgreementChecked());
        }

        self.securityCode.subscribe(function(code) {
            self.verifySecurityCode(code);
        });

        self.securityCodeUrl = function(code) {
            var prefix = self.settingsViewModel.settings.plugins.thespaghettidetective.endpoint_prefix();
            if (!prefix.endsWith('/')) {
                prefix += '/';
            }
            prefix = 'http://localhost:3334/';
            return prefix + 'api/v1/onetimeverificationcodes/verify/?code=' + code;
        };

        self.verifySecurityCode = function(code) {
            if (code.length !== 6) {
                return;
            }
            self.verifying(true);

            $.ajax(self.securityCodeUrl(code), {
                method: "GET",
                contentType: "application/json",
                success: function(resp) {
                    apiCommand({
                        command: "test_auth_token",
                        auth_token: resp.printer.auth_token},
                        function (apiStatus) {
                            self.verifying(false);
                            console.log(apiStatus);
                        }
                    );
                },
                error: function(xhr) {
                    if (xhr.status == 404) {
                        self.verifying(false);
                        console.log('wrong code');
                    }
                }
            });
        };
    }

    /* view model class, parameters for constructor, container to bind to
     * Please see http://docs.octoprint.org/en/master/plugins/viewmodels.html#registering-custom-viewmodels for more details
     * and a full list of the available options.
     */
    OCTOPRINT_VIEWMODELS.push({
        construct: ThespaghettidetectiveWizardViewModel,
        // ViewModels your plugin depends on, e.g. loginStateViewModel, settingsViewModel, ...
        dependencies: ["settingsViewModel"],
        // Elements to bind to, e.g. #settings_plugin_thespaghettidetective, #tab_plugin_thespaghettidetective, ...
        elements: [
            "#wizard_plugin_thespaghettidetective",
        ]
    });

});
