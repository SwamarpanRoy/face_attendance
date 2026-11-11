import QtQuick 2.15
import QtQuick.Layouts 1.15
import QtGraphicalEffects 1.15

// Course -> section -> period: progress bar, big title, large card choices.
Item {
    id: root
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 0

        Row {
            Layout.fillWidth: true
            spacing: 6
            Repeater {
                model: 3
                Rectangle {
                    width: (root.width - 48 - 12) / 3
                    height: 4
                    radius: 2
                    color: index <= device.startStep ? Theme.text : Qt.rgba(1, 1, 1, 0.12)
                    Behavior on color { ColorAnimation { duration: 200 } }
                }
            }
        }
        Text {
            text: "STEP " + (device.startStep + 1) + " OF 3"
            color: Theme.subtle
            font.family: Theme.font
            font.pixelSize: 12
            font.weight: Font.DemiBold
            font.letterSpacing: 1
            Layout.topMargin: 18
        }
        Text {
            text: device.startTitle
            color: Theme.text
            font.family: Theme.font
            font.pixelSize: 30
            font.weight: Font.Bold
            Layout.topMargin: 6
        }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 20
            clip: true
            spacing: 10
            model: device.startItems
            currentIndex: device.startIndex
            boundsBehavior: Flickable.StopAtBounds
            delegate: Rectangle {
                readonly property bool chosen: index === device.startIndex
                width: list.width
                height: 68
                radius: 20
                color: chosen ? Theme.primary : Theme.surface
                border.width: chosen ? 0 : 1
                border.color: Theme.hairline
                scale: tap.pressed ? 0.98 : 1
                Behavior on scale { NumberAnimation { duration: 120 } }
                Behavior on color { ColorAnimation { duration: 160 } }
                Text {
                    anchors { left: parent.left; right: tick.left; leftMargin: 22; rightMargin: 12
                              verticalCenter: parent.verticalCenter }
                    text: modelData
                    color: parent.chosen ? Theme.primaryText : Theme.text
                    font.family: Theme.font
                    font.pixelSize: 18
                    font.weight: Font.Medium
                    elide: Text.ElideRight
                }
                Icon {
                    id: tick
                    visible: parent.chosen
                    name: "check"
                    size: 22
                    color: Theme.primaryText
                    anchors { right: parent.right; rightMargin: 22; verticalCenter: parent.verticalCenter }
                }
                MouseArea { id: tap; anchors.fill: parent; onClicked: device.startSelect(index) }
            }
        }

        Text {
            visible: device.startHint !== ""
            text: device.startHint
            color: "#FCA5A5"
            font.family: Theme.font
            font.pixelSize: 14
            font.weight: Font.Medium
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
            Layout.topMargin: 12
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 16
            spacing: 12
            Button { variant: "ghost"; text: "Back"; Layout.preferredWidth: 120
                     onClicked: device.startBack() }
            Button {
                Layout.fillWidth: true
                variant: "primary"
                text: device.startStep === 2 ? "Start" : "Next"
                icon: device.startStep === 2 ? "play" : ""
                onClicked: device.startNext()
            }
        }
    }
}
