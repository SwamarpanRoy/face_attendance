import QtQuick 2.15
import QtQuick.Layouts 1.15
import QtGraphicalEffects 1.15

// Home: device + server status, large clock, one stats card, the session button.
Item {
    id: root
    property var now: new Date()
    Timer { interval: 1000; running: root.visible; repeat: true; triggeredOnStart: true
            onTriggered: root.now = new Date() }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Text {
                text: device.deviceName
                color: Theme.muted
                font.family: Theme.font
                font.pixelSize: 15
                font.weight: Font.DemiBold
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Pill {
                text: device.serverText
                tone: device.serverState === "online" ? "ok"
                    : device.serverState === "unset" ? "warn" : "error"
            }
        }

        Item { Layout.fillHeight: true; Layout.preferredHeight: 2 }

        Text {
            text: Qt.formatTime(root.now, "hh:mm")
            color: Theme.text
            font.family: Theme.font
            font.pixelSize: 112
            font.weight: Font.Light
            font.letterSpacing: -4
            Layout.alignment: Qt.AlignHCenter
        }
        Text {
            text: Qt.formatDate(root.now, "dddd, d MMMM")
            color: Theme.muted
            font.family: Theme.font
            font.pixelSize: 18
            font.weight: Font.Medium
            Layout.alignment: Qt.AlignHCenter
        }
        Item { Layout.preferredHeight: 18 }
        Rectangle {  // calibration / clock notice, quiet
            id: notice
            visible: device.idleWarning !== ""
            Layout.alignment: Qt.AlignHCenter
            Layout.maximumWidth: parent.width
            implicitWidth: Math.min(noticeText.implicitWidth + 28, root.width - 48)
            implicitHeight: noticeText.implicitHeight + 14
            radius: 16
            color: Qt.rgba(0.96, 0.62, 0.04, 0.10)
            border.color: Qt.rgba(0.96, 0.62, 0.04, 0.25)
            Text {
                id: noticeText
                anchors.centerIn: parent
                width: Math.min(implicitWidth, root.width - 76)
                text: device.idleWarning
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                color: "#FCD34D"
                font.family: Theme.font
                font.pixelSize: 13
                font.weight: Font.Medium
            }
        }
        Text {
            visible: device.message !== ""
            text: device.message
            color: Theme.muted
            font.family: Theme.font
            font.pixelSize: 14
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            Layout.fillWidth: true
            Layout.topMargin: 10
        }

        Item { Layout.fillHeight: true; Layout.preferredHeight: 3 }

        Rectangle {  // one card, three stats
            Layout.fillWidth: true
            implicitHeight: 92
            radius: 22
            color: Theme.surface
            border.color: Theme.hairline
            layer.enabled: true
            layer.effect: DropShadow { transparentBorder: true; radius: 24; samples: 41
                                       verticalOffset: 8; color: "#66000000" }
            RowLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 0
                Repeater {
                    model: [[String(device.queueLength), "Waiting"],
                            [device.lastSync, "Last sync"],
                            [device.wifi, "WiFi · " + device.ip]]
                    delegate: RowLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        spacing: 0
                        Rectangle { visible: index > 0; width: 1; Layout.fillHeight: true
                                    color: Theme.hairline; Layout.rightMargin: 14 }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                text: modelData[0]
                                color: Theme.text
                                font.family: Theme.font
                                font.pixelSize: 20
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Text {
                                text: modelData[1]
                                color: Theme.subtle
                                font.family: Theme.font
                                font.pixelSize: 12
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                    }
                }
            }
        }

        Item { Layout.preferredHeight: 20 }
        Button {
            visible: device.resumable
            Layout.fillWidth: true
            Layout.bottomMargin: 12
            variant: "accent"
            text: "Resume session"
            icon: "rotate-ccw"
            onClicked: device.resumeSession()
        }
        Button {
            Layout.fillWidth: true
            variant: "primary"
            text: "Start session"
            icon: "play"
            onClicked: device.startSession()
        }
        Button {
            visible: device.testAvailable
            Layout.fillWidth: true
            Layout.topMargin: 12
            text: "Test recognition"
            icon: "scan-face"
            onClicked: device.testRecognition()
        }
        Item { Layout.preferredHeight: 12 }
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Button { Layout.fillWidth: true; text: "Enrol"; icon: "user-plus"
                     onClicked: device.openEnrol() }
            Button { Layout.fillWidth: true; text: "Settings"; icon: "settings"
                     onClicked: device.openSettings() }
        }
    }
}
