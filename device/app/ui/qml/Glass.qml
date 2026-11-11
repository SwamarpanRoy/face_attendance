import QtQuick 2.15
import QtGraphicalEffects 1.15

// Real frosted glass: blurs the part of `backdrop` behind this panel, clips it to a
// rounded rectangle and lays a tint and a lit edge on top. The panel can sit anywhere in
// the tree: its rectangle is mapped into the backdrop's coordinates.
Item {
    id: root
    property Item backdrop
    property real radius: 22
    property color tint: Qt.rgba(0.09, 0.09, 0.11, 0.45)
    property color edge: Qt.rgba(1, 1, 1, 0.16)
    property real blur: 64
    default property alias content: holder.data
    // Where this panel is on the backdrop; re-evaluated when it or its parent moves.
    readonly property rect area: {
        var moved = [x, y, width, height, parent ? parent.x : 0, parent ? parent.y : 0]
        if (!backdrop) return Qt.rect(0, 0, 0, 0)
        var p = mapToItem(backdrop, 0, 0)
        return Qt.rect(p.x, p.y, width, height)
    }

    ShaderEffectSource {
        id: src
        anchors.fill: parent
        sourceItem: root.backdrop
        sourceRect: root.area
        visible: false
    }
    FastBlur { id: blurred; anchors.fill: parent; source: src; radius: root.blur; visible: false }
    Rectangle { id: mask; anchors.fill: parent; radius: root.radius; visible: false }
    OpacityMask { anchors.fill: parent; source: blurred; maskSource: mask }
    Rectangle {
        anchors.fill: parent
        radius: root.radius
        color: root.tint
        border.width: 1
        border.color: root.edge
    }
    Rectangle {  // highlight along the top rim
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 1 }
        height: Math.min(parent.height / 2, 28)
        radius: root.radius
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.10) }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }
    Item { id: holder; anchors.fill: parent }
}
