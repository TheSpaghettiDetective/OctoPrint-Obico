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

    function TheSpaghettiDetectiveSettingsViewModel(parameters) {
        var self = this;

        // assign the injected parameters, e.g.:
        // self.loginStateViewModel = parameters[0];
        self.settingsViewModel = parameters[0];

        self.showDetailPage = ko.observable(false);
        self.streaming = ko.mapping.fromJS({ is_pro: false, is_pi_camera: false });

        self.onStartupComplete = function (plugin, data) {
            self.fetchPluginStatus();
        }

        self.fetchPluginStatus = function () {
            apiCommand({
                command: "get_plugin_status",
            })
            .done(function (data) {
                ko.mapping.fromJS(data.streaming_status, self.streaming);

                if (_.get(data, 'sentry_opt') === "out") {
                    var sentrynotice = new PNotify({
                        title: "The Spaghetti Detective",
                        text: "<p>Turn on bug reporting to help us make TSD plugin better?</p><p>The debugging info included in the report will be anonymized.</p>",
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
                _.get(data, 'alerts', []).forEach(function (alertMsg) {
                    self.displayAlert(alertMsg);
                })
            });
        }

        self.toggleSentryOpt = function (ev) {
            apiCommand({
                command: "toggle_sentry_opt",
            });
            return true;
        };

        self.selectPage = function(page) {
            self.showDetailPage(true);

            switch (page) {
                case 'troubleshooting':
                    $('li[data-page="advanced"]').removeClass('active');
                    $('#advanced').removeClass('active');
                    $('li[data-page="troubleshooting"]').addClass('active');
                    $('#troubleshooting').addClass('active');
                    break;
                case 'advanced':
                    $('li[data-page="troubleshooting"]').removeClass('active');
                    $('#troubleshooting').removeClass('active');
                    $('li[data-page="advanced"]').addClass('active');
                    $('#advanced').addClass('active');
                    break;
            }
        };

        $(function() {
            $('.settings-wrapper .toggle').click(function() {
                $(this).toggleClass('opened');
            })
        });

        /*** Plugin error alerts */

    }

    /* view model class, parameters for constructor, container to bind to
     * Please see http://docs.octoprint.org/en/master/plugins/viewmodels.html#registering-custom-viewmodels for more details
     * and a full list of the available options.
     */
    OCTOPRINT_VIEWMODELS.push({
        construct: TheSpaghettiDetectiveSettingsViewModel,
        // ViewModels your plugin depends on, e.g. loginStateViewModel, settingsViewModel, ...
        dependencies: ["settingsViewModel"],
        // Elements to bind to, e.g. #settings_plugin_thespaghettidetective, #tab_plugin_thespaghettidetective, ...
        elements: [
            "#settings_plugin_thespaghettidetective",
        ]
    });

});
