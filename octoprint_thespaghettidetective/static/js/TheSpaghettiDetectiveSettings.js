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

        self.settingsPageSelected = ko.observable(false);

        self.selectPage = function(page) {
            self.settingsPageSelected(true);

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


        // assign the injected parameters, e.g.:
        // self.loginStateViewModel = parameters[0];
        self.settingsViewModel = parameters[0];

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
