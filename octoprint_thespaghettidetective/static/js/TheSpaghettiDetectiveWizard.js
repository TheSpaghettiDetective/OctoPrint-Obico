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
        self.mobileFlow = ko.observable(true);
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

        $(document).keydown(function(e) {
            if (self.step() === 4) {
                let availableInputs = ['0','1','2','3','4','5','6','7','8','9'];

                if (e.keyCode === 8) {
                    // Backspace
                    for (let i = 6; i >= 1; i--) {
                        let input = $('#verification-code input[data-number='+ i +']');
                        if (input.val()) {
                            input.val('');
                            self.securityCode(self.securityCode().slice(0, -1));
                            break;
                        }
                    }
                } else if (availableInputs.includes(e.key)) {
                    // Normal input
                    let allCellsFilled = false;

                    for (let i = 1; i <= 6; i++) {
                        let input = $('#verification-code input[data-number='+ i +']');
                        if (!input.val()) {
                            input.val(e.key);
                            self.securityCode(self.securityCode() + e.key);
                            allCellsFilled = (i === 6) ? true : false;
                            break;
                        }
                    }

                    if (allCellsFilled) {
                        // End of input
                        self.verifying(true);
                    }
                }

                if (self.securityCode().length < 6) {
                    // Return input to initial state
                    $('.verification-wrapper').removeClass(['text-error', 'text-success']);
                }
            }
        });

        self.securityCodeUrl = function(code) {
            var prefix = self.settingsViewModel.settings.plugins.thespaghettidetective.endpoint_prefix();
            if (!prefix.endsWith('/')) {
                prefix += '/';
            }
            return prefix + 'api/v1/onetimeverificationcode/verify/?code=' + code;
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
                            $('.verification-wrapper').addClass('success');
                            self.nextStep();
                        }
                    );
                },
                error: function(xhr) {
                    if (xhr.status == 404) {
                        self.verifying(false);
                        $('.verification-wrapper').addClass('error');
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
