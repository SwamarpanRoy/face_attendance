import QtQuick 2.15
import QtQuick.Layouts 1.15

// PIN entry: lock icon, title, dots that shake on a wrong PIN, round keypad.
Item {
    id: root
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 28
        spacing: 0
        Item { Layout.fillHeight: true }
        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            width: 64; height: 64; radius: 32
            color: Theme.surface
            border.color: Theme.hairline
            Icon { anchors.centerIn: parent; name: "lock"; size: 28 }
        }
        Text {
            text: device.pinTitle
            color: Theme.text
            font.family: Theme.font
            font.pixelSize: 28
            font.weight: Font.Bold
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 18
        }
        Text {
            text: device.pinMessage
            color: device.pinErrors > 0 && device.pinLength === 0 ? "#FCA5A5" : Theme.muted
            font.family: Theme.font
            font.pixelSize: 15
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            Layout.fillWidth: true
            Layout.topMargin: 6
        }
        Row {
            id: dots
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 28
            spacing: 16
            Repeater {
                model: Math.max(6, device.pinLength)
                Rectangle {
                    width: 14; height: 14; radius: 7
                    color: index < device.pinLength ? Theme.text : "transparent"
                    border.width: 2
                    border.color: index < device.pinLength ? Theme.text : Theme.subtle
                    Behavior on color { ColorAnimation { duration: 90 } }
                }
            }
            transform: Translate { id: nudge }
        }
        Connections {
            target: device
            function onPinChanged() {
                if (device.pinErrors !== root.lastErrors) {
                    root.lastErrors = device.pinErrors
                    wobble.restart()
                }
            }
        }
        Item { Layout.fillHeight: true }
        Keypad {
            Layout.alignment: Qt.AlignHCenter
            key: 84
            onDigit: device.pinKey(value)
            onBackspace: device.pinBackspace()
            onConfirm: device.pinSubmit()
        }
        Button {
            variant: "ghost"
            text: "Cancel"
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 12
            onClicked: device.pinCancel()
        }
    }
    property int lastErrors: 0
    SequentialAnimation {
        id: wobble
        NumberAnimation { target: nudge; property: "x"; to: -14; duration: 50 }
        NumberAnimation { target: nudge; property: "x"; to: 12; duration: 60 }
        NumberAnimation { target: nudge; property: "x"; to: -8; duration: 60 }
        NumberAnimation { target: nudge; property: "x"; to: 0; duration: 50 }
    }
}
