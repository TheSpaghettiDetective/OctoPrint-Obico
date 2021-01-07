/*
 * View model for TheSpaghettiDetective Wizard
 *
 * Author: The Spaghetti Detective
 * License: AGPLv3
 */
$(function () {

    function apiCommand(data) {
        return $.ajax("api/plugin/thespaghettidetective", {
            method: "POST",
            contentType: "application/json",
            data: JSON.stringify(data)
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
                    for (let i = 1; i <= 6; i++) {
                        let input = $('#verification-code input[data-number='+ i +']');
                        if (!input.val()) {
                            input.val(e.key);
                            self.securityCode(self.securityCode() + e.key);
                            break;
                        }
                    }
                }

                if (self.securityCode().length < 6) {
                    // Return input to initial state
                    $('.verification-wrapper').removeClass(['error', 'success', 'unknown']);
                }
            }
        });

        self.verifySecurityCode = function(code) {
            if (code.length !== 6) {
                return;
            }
            self.verifying(true);

            apiCommand({
                command: "verify_code",
                code: code})
                .done(function(apiStatus) {
                    if (apiStatus.succeeded) {
                        $('.verification-wrapper').addClass('success');
                        self.nextStep();
                    } else {
                        $('.verification-wrapper').addClass('error');
                    }
                })
                .fail(function() {
                    $('.verification-wrapper').addClass('unknown');
                })
                .always(function () {
                    self.verifying(false);
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
