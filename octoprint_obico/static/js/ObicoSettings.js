/*
 * View model for Obico Settings
 *
 * Author:  The Obico team
 * License: AGPLv3
 */
$(function () {

    $(function() {
        $('.obico-collapsable__title').click(function() {
            $(this).parent().toggleClass('opened');
        })
    });

    function ObicoSettingsViewModel(parameters) {
        var self = this;

        const defaultServerAddress = 'https://app.obico.io';
        function getServerType(serverAddress) {
            return serverAddress == defaultServerAddress ? 'cloud' : 'self-hosted';
        }

        // assign the injected parameters, e.g.:
        // self.loginStateViewModel = parameters[0];
        self.obicoWizardViewModel = parameters[0];
        self.settingsViewModel = parameters[1];
        self.wizardViewModel = parameters[2];

        self.alertsShown = {};
        self.piCamResolutionOptions = [{ id: "low", text: "Low" }, { id: "medium", text: "Medium" }, { id: "high", text: "High" }, { id: "ultra_high", text: "Ultra High" }];
        self.isWizardShown = ko.observable(
            retrieveFromLocalStorage('disableTSDWizardAutoPopupUntil', 0) > new Date().getTime()
        );
        self.showDetailPage = ko.observable(false);
        self.serverStatus = ko.mapping.fromJS({ is_connected: false, status_posted_to_server_ts: 0, bailed_because_tsd_plugin_running: false });
        self.streaming = ko.mapping.fromJS({ is_pi_camera: false, webrtc_streaming: false, compat_streaming: false});
        self.linkedPrinter = ko.mapping.fromJS({ is_pro: false, id: null, name: null});
        self.errorStats = ko.mapping.fromJS({ server: { attempts: 0, error_count: 0, first: null, last: null }, webcam: { attempts: 0, error_count: 0, first: null, last: null }});
        self.serverTestStatusCode = ko.observable(null);
        self.serverTested = ko.observable('never');
        self.sentryOptedIn = ko.pureComputed(function () {
            return self.settingsViewModel.settings.plugins.obico.sentry_opt() === "in";
        }, self);
        self.configured = ko.pureComputed(function () {
            return self.settingsViewModel.settings.plugins.obico.auth_token
                && self.settingsViewModel.settings.plugins.obico.auth_token();
        }, self);
        self.wizardAutoPoppedup = ko.observable(false);
        self.disableWizardAutoPopUp = ko.observable(false);
        self.serverType = ko.observable('cloud');
        self.hasTsdMigratedModalShown = ko.observable(false);

        self.onStartupComplete = function (plugin, data) {
            self.fetchPluginStatus();
            self.serverType(getServerType(self.settingsViewModel.settings.plugins.obico.endpoint_prefix()));
        };

        self.onSettingsBeforeSave = function() {
            self.serverType(getServerType(self.settingsViewModel.settings.plugins.obico.endpoint_prefix()));
        }

        self.hasServerErrors = function() {
            return self.errorStats.server.error_count() > 0;
        };

        self.hasWebcamErrors = function() {
            return self.errorStats.webcam.error_count() > 0;
        };

        self.serverErrorRate = function() {
            return Math.round(self.errorStats.server.error_count() / self.errorStats.server.attempts() * 100);
        };

        self.webcamErrorRate = function() {
            return Math.round(self.errorStats.webcam.error_count() / self.errorStats.webcam.attempts() * 100);
        };

        self.serverTestSucceeded = function() {
            return self.serverTestStatusCode() == 200;
        };

        self.serverTestUnknownError = function() {
            return self.serverTestStatusCode() != null && self.serverTestStatusCode() != 401 && self.serverTestStatusCode() != 200;
        };

        self.fetchPluginStatus = function() {
            apiCommand({
                command: "get_plugin_status",
            })
            .done(function (data) {
                ko.mapping.fromJS(data.server_status, self.serverStatus);
                ko.mapping.fromJS(data.streaming_status, self.streaming);
                ko.mapping.fromJS(data.error_stats, self.errorStats);
                ko.mapping.fromJS(data.linked_printer, self.linkedPrinter);

                _.get(data, 'alerts', []).forEach(function (alertMsg) {
                    self.displayAlert(alertMsg);
                })

                if (self.settingsViewModel.settings.plugins.obico.tsd_migrated
                    && self.settingsViewModel.settings.plugins.obico.tsd_migrated() == 'yes'
                    && !self.hasTsdMigratedModalShown()) {
                    self.showTsdMigratedModal();
                    self.hasTsdMigratedModalShown(true);
                    return;
                }

                if (!self.configured() && !self.isWizardShown() && !self.wizardViewModel.isDialogActive()) {
                    self.showWizardModal();
                    self.isWizardShown(true);
                    return;
                }

                if (_.get(data, 'sentry_opt') === "out") {
                    var sentrynotice = new PNotify({
                        title: "Obico",
                        text: "<p>Turn on bug reporting to help us make Obico plugin better?</p><p>The debugging info included in the report will be anonymized.</p>",
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
        };

        self.testServerConnection = function() {
            self.serverTested('testing');
            apiCommand({
                command: "test_server_connection",
            })
            .done(function (data) {
                self.serverTested('tested');
                self.serverTestStatusCode(data.status_code);
            });
        };

        self.toggleSentryOpt = function (ev) {
            apiCommand({
                command: "toggle_sentry_opt",
            });
            return true;
        };

        self.resetEndpointPrefix = function () {
            self.settingsViewModel.settings.plugins.obico.endpoint_prefix(defaultServerAddress);
            return true;
        };

        self.clearEndpointPrefix = function () {
            self.settingsViewModel.settings.plugins.obico.endpoint_prefix('');
            return true;
        };

        self.selectPage = function(page) {
            self.showDetailPage(true);

            switch (page) {
                case 'troubleshooting':
                    $('li[data-page="advanced"]').removeClass('active');
                    $('#obico-advanced').removeClass('active');
                    $('li[data-page="troubleshooting"]').addClass('active');
                    $('#obico-troubleshooting').addClass('active');
                    break;
                case 'advanced':
                    $('li[data-page="troubleshooting"]').removeClass('active');
                    $('#obico-troubleshooting').removeClass('active');
                    $('li[data-page="advanced"]').addClass('active');
                    $('#obico-advanced').addClass('active');
                    break;
            }
        };

        self.returnToSelection = function() {
            self.showDetailPage(false);
        }

        /*** Plugin error alerts */

        self.onDataUpdaterPluginMessage = function (plugin, data) {
            if (plugin != "obico") {
                return;
            }

            if (data.plugin_updated) {
                self.fetchPluginStatus();
            }

            if (data.printer_autolinked) {
                self.fetchPluginStatus();
                self.obicoWizardViewModel.toStep(5);
                self.obicoWizardViewModel.startAutoCloseTimout();
            }
        }

        self.displayAlert = function (alertMsg) {

            var AVAILABLE_BUTTONS = {
                more_info: {
                    text: "More Info",
                    click: function (notice) {
                        window.open(alertMsg.info_url, '_blank');
                        notice.remove();
                    },
                },
                never: {
                    text: "Never Show Again",
                    click: function (notice) {
                        saveToLocalStorage(ignoredItemPath, true);
                        notice.remove();
                    },
                    addClass: "never_button"
                },
                ok: {
                    text: "OK",
                    click: function (notice) {
                        notice.remove();
                    },
                    addClass: "ok_button"
                },
                close: {
                    text: "Close",
                    addClass: "remove_button"
                },
                diagnose: {
                    text: "Details",
                    click: function (notice) {
                        self.showDiagnosticReportModal();
                        notice.remove();
                    }
                },
            };


            var ignoredItemPath = "ignored." + alertMsg.cause + "." + alertMsg.level;
            if (retrieveFromLocalStorage(ignoredItemPath, false)) {
                return;
            }

            var showItemPath = alertMsg.cause + "." + alertMsg.level;
            if (_.get(self.alertsShown, showItemPath, false)) {
                return;
            }
            _.set(self.alertsShown, showItemPath, true);

            var msgType = "error";
            if (alertMsg.level === "warning") {
                msgType = "notice";
            }

            var buttons = _.map(alertMsg.buttons, function(k) {
                return AVAILABLE_BUTTONS[k];
            });

            hiddenButtons = [];

            // PNotify needs this dance to hide the default "Ok" and "Cancel" button
            ['ok', 'close'].forEach(function(k) {
                if (!_.includes(alertMsg.buttons, k)) {
                    buttons.push(AVAILABLE_BUTTONS[k]);
                    hiddenButtons.push(AVAILABLE_BUTTONS[k].addClass);
                }
            })

            if (alertMsg.text) {
                new PNotify({
                    title: "Obico",
                    text: alertMsg.text,
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
                        hiddenButtons.forEach(function(className) {
                            notice
                                .get()
                                .find("." + className)
                                .remove();
                        });
                    }
                });
            }
        };

        self.showDiagnosticReportModal = function () {
            $('#diagnosticReportModal').modal();
        };

        self.showWizardModal = function (maybeViewModel) {
            self.wizardAutoPoppedup(!Boolean(maybeViewModel)); // When it's triggered by user click, maybeViewModel is not undefined
            $('#wizardModal').modal({backdrop: 'static', keyboard: false});
        };

        $('#wizardModal').on('shown', function(){
            self.obicoWizardViewModel.reset();
        });
        $('#wizardModal').on('hidden', function(){
            if (self.disableWizardAutoPopUp()) {
                saveToLocalStorage('disableTSDWizardAutoPopupUntil', (new Date()).getTime() + 1000*60*60*24*30); // Not show for 30 days
            }
        });

        self.showTsdMigratedModal = function (maybeViewModel) {
            $('#tsdMigratedModal').modal();
        };
        self.hideTsdMigratedModal = function() {
            $('#tsdMigratedModal').modal('hide');
            self.settingsViewModel.saveData({plugins: {obico: {tsd_migrated: 'confirmed'}}});
        }
    }


    // Helper methods
    function apiCommand(data) {
        return $.ajax("api/plugin/obico", {
            method: "POST",
            contentType: "application/json",
            data: JSON.stringify(data)
        });
    }

    var LOCAL_STORAGE_KEY = 'plugin.obico';

    function localStorageObject() {
        var retrievedObject = localStorage.getItem(LOCAL_STORAGE_KEY);
        if (!retrievedObject) {
            retrievedObject = '{}';
        }
        return JSON.parse(retrievedObject);
    }

    function retrieveFromLocalStorage(itemPath, defaultValue) {
        return _.get(localStorageObject(), itemPath, defaultValue);
    }

    function saveToLocalStorage(itemPath, value) {
        var retrievedObject = localStorageObject();
        _.set(retrievedObject, itemPath, value);
        localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(retrievedObject));
    }


    /* view model class, parameters for constructor, container to bind to
     * Please see http://docs.octoprint.org/en/master/plugins/viewmodels.html#registering-custom-viewmodels for more details
     * and a full list of the available options.
     */
    OCTOPRINT_VIEWMODELS.push({
        construct: ObicoSettingsViewModel,
        // ViewModels your plugin depends on, e.g. loginStateViewModel, settingsViewModel, ...
        dependencies: ["obicoWizardViewModel", "settingsViewModel", "wizardViewModel"],
        // Elements to bind to, e.g. #settings_plugin_obico, #tab_plugin_obico, ...
        elements: [
            "#settings_plugin_obico",
        ]
    });

});
