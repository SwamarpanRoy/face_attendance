import QtQuick 2.15
import QtQuick.Layouts 1.15
import QtGraphicalEffects 1.15

// Device information (scrolls if long) and the four admin actions.
Item {
    id: root
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 0
        Text { text: "DEVICE"; color: Theme.subtle; font.family: Theme.font; font.pixelSize: 12
               font.weight: Font.DemiBold; font.letterSpacing: 1 }
        Text { text: "Settings"; color: Theme.text; font.family: Theme.font; font.pixelSize: 30
               font.weight: Font.Bold; Layout.topMargin: 6 }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 18
            radius: 22
            color: Theme.surface
            border.color: Theme.hairline
            layer.enabled: true
            layer.effect: DropShadow { transparentBorder: true; radius: 24; samples: 41
                                       verticalOffset: 8; color: "#66000000" }
            ListView {
                id: rows
                anchors.fill: parent
                anchors.margins: 6
                clip: true
                model: device.settingsRows
                boundsBehavior: Flickable.StopAtBounds
                delegate: Item {
                    width: rows.width
                    height: Math.max(40, value.implicitHeight + 18)
                    Text {
                        id: key
                        x: 14; width: 104
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData.key
                        color: Theme.subtle
                        font.family: Theme.font
                        font.pixelSize: 13
                    }
                    Text {
                        id: value
                        anchors { left: key.right; right: parent.right; rightMargin: 14
                                  verticalCenter: parent.verticalCenter }
                        text: modelData.value
                        color: Theme.text
                        font.family: Theme.font
                        font.pixelSize: 13
                        wrapMode: Text.WrapAnywhere
                    }
                    Rectangle { visible: index > 0; x: 14; width: parent.width - 28; height: 1
                                color: Theme.hairline }
                }
            }
        }

        Rectangle {
            visible: device.settingsWarning !== ""
            Layout.fillWidth: true
            Layout.topMargin: 12
            implicitHeight: warn.implicitHeight + 20
            radius: 16
            color: Qt.rgba(0.96, 0.62, 0.04, 0.10)
            border.color: Qt.rgba(0.96, 0.62, 0.04, 0.25)
            Text { id: warn; anchors.fill: parent; anchors.margins: 10
                   text: device.settingsWarning; color: "#FCD34D"; font.family: Theme.font
                   font.pixelSize: 13; font.weight: Font.Medium; wrapMode: Text.WordWrap }
        }
        Text {
            visible: device.settingsFeedback !== ""
            text: device.settingsFeedback
            color: Theme.muted
            font.family: Theme.font
            font.pixelSize: 13
            Layout.topMargin: 8
        }

        GridLayout {
            Layout.fillWidth: true
            Layout.topMargin: 16
            columns: 2
            columnSpacing: 12
            rowSpacing: 12
            Button { Layout.fillWidth: true; text: "Sync now"; icon: "refresh-cw"
                     onClicked: device.syncNow() }
            Button { Layout.fillWidth: true; text: "Test camera"; icon: "camera"
                     onClicked: device.testCamera() }
            Button { Layout.fillWidth: true; text: "Restart app"; icon: "rotate-ccw"
                     onClicked: device.restartApp() }
            Button { Layout.fillWidth: true; Layout.preferredHeight: 64; variant: "primary"
                     text: "Done"; icon: "check"; onClicked: device.show_idle() }
        }
    }
}
