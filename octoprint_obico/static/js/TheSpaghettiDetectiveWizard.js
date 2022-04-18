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

        const defaultServerAddress = 'https://app.thespaghettidetective.com';
        function getServerType(serverAddress) {
            return serverAddress == defaultServerAddress ? 'cloud' : 'self-hosted';
        }

        // assign the injected parameters, e.g.:
        // self.loginStateViewModel = parameters[0];
        self.settingsViewModel = parameters[0];

        self.step = ko.observable(1);
        self.mobileFlow = ko.observable(true);
        self.securityCode = ko.observable('');
        self.verifying = ko.observable(false);
        self.userAgreementChecked = ko.observable(true);
        self.printerName = ko.observable('');
        self.printerNameTimeoutId = ko.observable(null);
        self.currentFeatureSlide = ko.observable(1);

        self.serverType = ko.observable('cloud');

        self.onStartupComplete = function () {
            self.serverType(getServerType(self.settingsViewModel.settings.plugins.thespaghettidetective.endpoint_prefix()));
        };

        self.isServerInvalid = ko.observable(false);

        self.checkSeverValidity = (url) => {
            return /^https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)$/.test(url);
        }

        // Handle verification code typing:

        $.find('.tsd-verification-code-input input').forEach(function(input) {
            // Add event listener for paste event
            $(input).on('paste', handlePaste);

            // Handle typing in verification code to automatically move cursor back/forward
            $(input).on('keydown', handleKeydown);
        });

        // Paste verification code digit by digit
        function handlePaste(event) {
            let text = (event.originalEvent.clipboardData || window.clipboardData).getData('text');
            for (let i = 0; i < text.length; i++) {
                let char = text[i];
                appendToVerificationCode(char);
            }

            event.preventDefault();
        }

        // Move cursor back/forward
        function handleKeydown(event) {

            // Skip Paste event
            if (event.key === 'Meta' || event.key === 'Control' || event.originalEvent.code === 'KeyV') {
                return true;
            }

            if (event.key === 'Backspace') {
                for (let i = 6; i >= 1; i--) {
                    let input = $('.tsd-verification-code-input input[data-number='+ i +']');
                    if (input.val()) {
                        input.val('').trigger('focus');
                        self.securityCode(self.securityCode().slice(0, -1));
                        clearVerificationCodeMessages();
                        break;
                    }
                }
            } else {
                appendToVerificationCode(event.key);
            }

            return false;
        }

        // Append digit to verification code
        function appendToVerificationCode(char) {
            let availableInputs = ['0','1','2','3','4','5','6','7','8','9'];

            if (!availableInputs.includes(char)) {
                return false;
            }

            for (let i = 1; i <= 6; i++) {
                let input = $('.tsd-verification-code-input input[data-number='+ i +']');

                if (!input.val()) {
                    // Put value to input visible to user
                    input.val(char);

                    // Move cursor forward
                    if (i < 6) {
                        $('.tsd-verification-code-input input[data-number='+ (i + 1) +']').trigger('focus');
                    }

                    // Append char to inner code
                    self.securityCode(self.securityCode() + char);

                    // Clear all error and other messages for the input
                    if (self.securityCode().length < 6) {
                        clearVerificationCodeMessages();
                    }

                    return true;
                }
            }

            return false;
        }

        // Clear error messages from verification code
        function clearVerificationCodeMessages() {
            $('.tsd-verification-wrapper').removeClass(['error', 'success', 'unknown']);
        }

        self.startAutoCloseTimout = function() {
            setTimeout(function() {
                $('.tsd-auto-close').addClass('active');
                setTimeout(function() { self.hideWizardModal(); }, 10000); // auto-close in 10s
            }, 500) // Add .5s delay to make transition visible
        }


        self.nextStep = function() {
            if (self.step() === 1) {
                let url = self.settingsViewModel.settings.plugins.thespaghettidetective.endpoint_prefix()

                if (self.serverType() === 'self-hosted') {
                    if (self.checkSeverValidity(url)) {
                        self.isServerInvalid(false);
                    } else {
                        self.isServerInvalid(true);
                        return;
                    }
                }

                self.serverType(getServerType(url))
                self.settingsViewModel.saveData({plugins: {thespaghettidetective: {endpoint_prefix: url}}});
            }

            self.toStep(self.step() + 1);

            if (self.step() === 4) {
                $('.tsd-modal[aria-hidden="false"] .tsd-verification-code-input input[data-number=1]').trigger('focus');
            } else if (self.step() === 5) {
                // Close button with countdown animation
                self.startAutoCloseTimout()
            }
        };

        self.prevStep = function() {
            self.toStep(self.step() - 1);
        };

        self.toStep = function(nextStep) {
            self.step(nextStep);
        };


        self.toggleCheck = function() {
            self.userAgreementChecked(!self.userAgreementChecked());
        }

        self.securityCode.subscribe(function(code) {
            self.verifySecurityCode(code);
        });

        self.printerName.subscribe(function() {
            if (self.printerNameTimeoutId()) {
                clearTimeout(self.printerNameTimeoutId());
            }
            let newTimeoutId = setTimeout(self.savePrinterName, 1000);
            self.printerNameTimeoutId(newTimeoutId);
        })

        self.verifySecurityCode = function(code) {
            if (code.length !== 6) {
                return;
            }
            self.verifying(true);

            apiCommand({
                command: "verify_code",
                code: code,
                endpoint_prefix: $('#endpoint_prefix-input').val(),
            })
                .done(function(apiStatus) {
                    if (apiStatus.succeeded == null) {
                        $('.tsd-verification-wrapper').addClass('unknown');
                    }
                    else if (apiStatus.succeeded) {
                        $('.tsd-verification-wrapper').addClass('success');
                        self.printerName(apiStatus.printer.name);
                        self.nextStep();
                    } else {
                        $('.tsd-verification-wrapper').addClass('error');
                    }
                })
                .fail(function() {
                    $('.tsd-verification-wrapper').addClass('unknown');
                })
                .always(function () {
                    self.verifying(false);
                });
        };

        self.resetEndpointPrefix = function () {
            self.settingsViewModel.settings.plugins.thespaghettidetective.endpoint_prefix(defaultServerAddress);
            return true;
        };

        self.clearEndpointPrefix = function () {
            self.settingsViewModel.settings.plugins.thespaghettidetective.endpoint_prefix('');
            return true;
        };

        self.reset = function() {
            self.step(1);
            self.verifying(false);
            self.securityCode('');
            $('.tsd-auto-close').removeClass('active');

            let verificationWrapper = $('.tsd-verification-wrapper');
            verificationWrapper.removeClass('success error unknown');

            for (let i = 1; i <= 6; i++) {
                verificationWrapper.find('.tsd-verification-code-input input[data-number='+ i +']').val('');
            }
        }

        self.hideWizardModal = function() {
            $('.tsd-auto-close').removeClass('active');
            $('#wizardModal').modal('hide');
        }

        // Next feature in the slider on home screen
        self.nextFeature = function() {
            let container = $('.tsd-features').last();
            let slidesCount = container.find('.tsd-feature').length;
            let currentSlide = self.currentFeatureSlide();
            let nextSlide = currentSlide === slidesCount ? 1 : currentSlide + 1;

            container.find('.tsd-feature[data-number="'+ currentSlide +'"]').animate({
                left: '-100%'
            }, {duration: 500, queue: false});

            container.find('.tsd-feature[data-number="'+ nextSlide +'"]').animate({
                left: '0'
            },
            500,
            function() {
                let next = nextSlide === slidesCount ? 1 : nextSlide + 1;
                container.find('.tsd-feature[data-number="'+ next +'"]').css('left', '100%');
            });

            self.currentFeatureSlide(nextSlide);
        }

        setInterval(self.nextFeature, 3000);
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
            "#tsd_wizard",
        ]
    });

});
