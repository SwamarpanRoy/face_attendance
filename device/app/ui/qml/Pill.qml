import QtQuick 2.15
import QtGraphicalEffects 1.15

// Status pill: glowing dot + text, tinted by tone (ok / warn / error / neutral).
Rectangle {
    id: root
    property string text
    property string tone: "neutral"
    property bool dot: true
    readonly property color accent: tone === "ok" ? Theme.success
                                  : tone === "warn" ? Theme.warning
                                  : tone === "error" ? Theme.danger : Theme.muted
    readonly property color ink: tone === "ok" ? "#6EE7B7" : tone === "warn" ? "#FCD34D"
                               : tone === "error" ? "#FCA5A5" : Theme.muted
    implicitWidth: row.implicitWidth + 28
    implicitHeight: 34
    radius: height / 2
    color: Qt.rgba(accent.r, accent.g, accent.b, 0.12)
    border.color: Qt.rgba(accent.r, accent.g, accent.b, 0.35)
    Row {
        id: row
        anchors.centerIn: parent
        spacing: 8
        Rectangle {
            visible: root.dot
            width: 8; height: 8; radius: 4
            color: root.accent
            anchors.verticalCenter: parent.verticalCenter
            layer.enabled: true
            layer.effect: Glow { radius: 8; samples: 17; color: root.accent; spread: 0.4 }
        }
        Text {
            text: root.text
            color: root.ink
            font.family: Theme.font
            font.pixelSize: 14
            font.weight: Font.DemiBold
        }
    }
}
