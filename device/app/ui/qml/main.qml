import QtQuick 2.15
import QtQuick.Window 2.15

// Root window: calm gradient background and the six screens, cross-faded by
// device.screen. `device` (DeviceController) and `uiVisible` come from
// device/app/ui/app.py.
Window {
    id: win
    width: 480
    height: 800
    visible: uiVisible  // fullscreen is applied from Python after loading (app.py)
    title: device.deviceName
    color: Theme.bgBottom

    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.bgTop }
            GradientStop { position: 1.0; color: Theme.bgBottom }
        }
    }

    component Page: Item {
        property string name
        readonly property bool shown: device.screen === name
        anchors.fill: parent
        opacity: shown ? 1 : 0
        visible: opacity > 0
        enabled: shown
        transform: Translate { y: shown ? 0 : 14; Behavior on y { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } } }
        Behavior on opacity { NumberAnimation { duration: 180 } }
    }

    Page { name: "idle"; IdleScreen { anchors.fill: parent } }
    Page { name: "start"; StartScreen { anchors.fill: parent } }
    Page { name: "pin"; PinScreen { anchors.fill: parent } }
    Page { name: "live"; LiveScreen { anchors.fill: parent } }
    Page { name: "settings"; SettingsScreen { anchors.fill: parent } }
    Page { name: "enrol"; EnrolScreen { anchors.fill: parent } }
}
