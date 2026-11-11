import QtQuick 2.15
import QtGraphicalEffects 1.15

// Round number pad (PIN entry, USN filter). Bottom row: backspace, 0, confirm.
Grid {
    id: root
    property real key: 84
    property bool showConfirm: true
    signal digit(string value)
    signal backspace()
    signal confirm()
    columns: 3
    columnSpacing: Math.round(key * 0.3)
    rowSpacing: Math.round(key * 0.2)
    Repeater {
        model: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "del", "0", "ok"]
        delegate: Rectangle {
            readonly property bool isOk: modelData === "ok"
            readonly property bool isDel: modelData === "del"
            width: root.key; height: root.key; radius: root.key / 2
            opacity: isOk && !root.showConfirm ? 0 : 1
            color: isOk ? Theme.primary : (isDel ? "transparent" : Theme.surface)
            border.width: isOk || isDel ? 0 : 1
            border.color: Theme.hairline
            scale: area.pressed ? 0.93 : 1
            Behavior on scale { NumberAnimation { duration: 110; easing.type: Easing.OutCubic } }
            layer.enabled: !isDel
            layer.effect: DropShadow { transparentBorder: true; radius: 16; samples: 33
                                       verticalOffset: 6; color: "#66000000" }
            Text {
                visible: !parent.isOk && !parent.isDel
                anchors.centerIn: parent
                text: modelData
                color: Theme.text
                font.family: Theme.font
                font.pixelSize: Math.round(root.key * 0.38)
                font.weight: Font.Light
            }
            Icon {
                visible: parent.isOk || parent.isDel
                anchors.centerIn: parent
                size: Math.round(root.key * 0.33)
                name: parent.isDel ? "delete" : "check"
                color: parent.isOk ? Theme.primaryText : Theme.muted
            }
            MouseArea {
                id: area
                anchors.fill: parent
                enabled: !(parent.isOk && !root.showConfirm)
                onClicked: parent.isOk ? root.confirm() : parent.isDel ? root.backspace()
                                                                       : root.digit(modelData)
            }
        }
    }
}
