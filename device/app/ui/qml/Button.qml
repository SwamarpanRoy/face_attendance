import QtQuick 2.15
import QtGraphicalEffects 1.15

// Touch button. variant: "primary" (white, soft shadow), "secondary" (raised dark surface),
// "accent" (green, e.g. Resume session), "ghost" (text only). Presses in when tapped.
Item {
    id: root
    property string text
    property string icon: ""
    property string variant: "secondary"
    property bool primary: variant === "primary"
    readonly property bool ghost: variant === "ghost"
    readonly property bool accent: variant === "accent"
    property color textColor: primary ? Theme.primaryText : accent ? "#ffffff"
                            : ghost ? Theme.muted : Theme.text
    property int fontSize: primary || accent ? 19 : 17
    signal clicked()
    implicitHeight: primary || accent ? 72 : 64
    implicitWidth: label.implicitWidth + 48
    opacity: enabled ? 1 : 0.4

    Rectangle {
        id: face
        anchors.fill: parent
        radius: root.primary || root.accent ? 22 : 20
        color: root.primary ? Theme.primary : root.accent ? Theme.success
             : root.ghost ? "transparent" : Theme.surface
        border.width: root.primary || root.accent || root.ghost ? 0 : 1
        border.color: Theme.hairline
        scale: area.pressed ? 0.97 : 1.0
        Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
        layer.enabled: !root.ghost
        layer.effect: DropShadow {
            transparentBorder: true
            radius: root.primary || root.accent ? 28 : 18
            samples: 41
            verticalOffset: root.primary || root.accent ? 10 : 6
            color: root.accent ? "#5510B981" : root.primary ? "#55000000" : "#70000000"
        }
        Rectangle {  // light catching the top edge of dark buttons
            visible: !root.primary && !root.accent && !root.ghost
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 1 }
            height: parent.height / 2
            radius: parent.radius
            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.06) }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }
        Row {
            id: label
            anchors.centerIn: parent
            spacing: 10
            Icon {
                visible: root.icon !== ""
                name: root.icon
                size: 22
                color: root.textColor
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                text: root.text
                color: root.textColor
                font.family: Theme.font
                font.pixelSize: root.fontSize
                font.weight: root.primary || root.accent ? Font.DemiBold : Font.Medium
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
    MouseArea { id: area; anchors.fill: parent; enabled: root.enabled; onClicked: root.clicked() }
}
