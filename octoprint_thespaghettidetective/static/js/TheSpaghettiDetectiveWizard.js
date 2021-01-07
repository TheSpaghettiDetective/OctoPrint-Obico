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

        self.startTypingCode = function() {
            $('.verification-wrapper').removeClass(['error', 'success']);
            self.securityCode('');
            $('#verification-code .front-layer').hide();
            $('#verification-code input:first-of-type').focus();
        }

        self.nextCell = function (data, event) {
            let number = parseInt($(event.target).attr('data-number'));

            if (event.keyCode === 8 && number !== 1) {
                // Backspace
                $(event.target).siblings('input[data-number='+ (number - 1) +']').val('').focus();
                self.securityCode(self.securityCode().substring(0, -1));
            } else {
                let val = $(event.target).val();
                self.securityCode(self.securityCode() + val);

                if (number === 6) {
                    // End of input
                    $(event.target).blur();
                    $('#verification-code input').each(function() {
                        $(this).val("");
                    });
                    $('#verification-code .front-layer').show();

                    self.verifying(true);
                } else {
                    // Type number and move to next cell
                    if (val) {
                        $(event.target).siblings('input[data-number='+ (number + 1) +']').focus();
                    }
                }
            }
        }

        $(document).keydown(function(e) {
            if (self.step() === 4) {
                // Backspace
                if (e.keyCode === 8) {
                    for (let i = 6; i >= 1; i--) {
                        let input = $('#verification-code input[data-number='+ i +']');
                        if (input.val()) {
                            input.val('');
                            self.securityCode(self.securityCode().substring(0, -1));
                            return;
                        }
                    }
                    return;
                }

                let allCellsFilled = false;

                for (let i = 1; i <= 6; i++) {
                    if (i === 1) {
                        // Return input to initial state
                        $('.verification-wrapper').removeClass(['error', 'success']);
                    }

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
