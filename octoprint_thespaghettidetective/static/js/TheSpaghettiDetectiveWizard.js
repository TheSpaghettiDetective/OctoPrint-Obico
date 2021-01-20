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
        self.printerName = ko.observable('');
        self.ctrlDown = ko.observable(false); // Handling Ctrl+V / Cmd+V commands
        self.currentFeatureSlide = ko.observable(1);

        let ctrlKey = 17, cmdKey = 91, vKey = 86;

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
                        let input = $('.verification-code-input input[data-number='+ i +']');
                        if (input.val()) {
                            input.val('');
                            self.securityCode(self.securityCode().slice(0, -1));
                            break;
                        }
                    }
                } else if (availableInputs.includes(e.key)) {
                    for (let i = 1; i <= 6; i++) {
                        let input = $('.verification-code-input input[data-number='+ i +']');
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

        // Next feature in the slider on home screen
        self.nextFeature = function(item, event) {
            let container = $(event.target).parents('.features');
            let slidesCount = container.find('.feature').length;
            let currentSlide = self.currentFeatureSlide();
            let nextSlide = currentSlide === slidesCount ? 1 : currentSlide + 1;

            
            console.log('slides count: ' + slidesCount);
            console.log('width: ' + container.width());

            console.log('current slide: ' + currentSlide);
            console.log('next slide: ' + nextSlide);

            
            container.find('.feature[data-number="'+ currentSlide +'"]').animate({
                left: '-100%'
            }, {duration: 500, queue: false});

            container.find('.feature[data-number="'+ nextSlide +'"]').animate({
                left: '0'
            },
            500,
            function() {
                let next = nextSlide === slidesCount ? 1 : nextSlide + 1;
                container.find('.feature[data-number="'+ next +'"]').css('left', '100%');
            });

            self.currentFeatureSlide(nextSlide);
        }

        
        // Functionality to handle Ctrl+V or Cmd+V commands

        document.addEventListener('keydown', function(e) {
            if (e.keyCode == ctrlKey || e.keyCode == cmdKey) self.ctrlDown = true;
        });
        document.addEventListener('keyup', function(e) {
            if (e.keyCode == ctrlKey || e.keyCode == cmdKey) self.ctrlDown = false;
        });

        document.addEventListener('keydown', function(e) {
            if (self.ctrlDown && (e.keyCode == vKey)) {
                self.pasteFromClipboard();
            }
        });

        self.pasteFromClipboard = function() {
            let format = new RegExp("\\d{6}");

            navigator.clipboard.readText()
            .then(text => {
                if (format.test(text)) {
                    $('.verification-wrapper').removeClass(['error', 'success', 'unknown']);
                    self.securityCode('');
                    for (let i = 1; i <= 6; i++) {
                        let input = $('.verification-code-input input[data-number='+ i +']');
                        input.val(text[i - 1]);
                        self.securityCode(self.securityCode() + text[i - 1]);
                    }
                }
            })
            .catch(err => {
                console.error('Failed to read clipboard contents: ', err);
            });
        };


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
                        self.printerName(apiStatus.printer.name);
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

        self.savePrinterName = function() {
            apiCommand({
                command: "update_printer",
                name: self.printerName()})
                .done(function(apiStatus) {
                    console.log(apiStatus);
                })
                .fail(function() {
                    $('.verification-wrapper').addClass('unknown');
                });
        }

        self.reset = function() {
            self.step(1);
            self.verifying(false);
            self.securityCode('');
            self.printerName('');
        }
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
