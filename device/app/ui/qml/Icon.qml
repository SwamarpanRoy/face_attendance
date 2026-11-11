import QtQuick 2.15
import QtQuick.Shapes 1.15
import "icons.js" as Icons

// A Lucide icon: the 24x24 path drawn as a round 2 px stroke, scaled to `size`.
Item {
    id: root
    property string name
    property color color: Theme.text
    property real size: 24
    width: size
    height: size
    Shape {
        width: 24
        height: 24
        scale: root.size / 24
        transformOrigin: Item.TopLeft
        layer.enabled: true
        layer.samples: 8
        layer.textureSize: Qt.size(root.size * 2, root.size * 2)
        ShapePath {
            strokeColor: root.color
            strokeWidth: 2
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg { path: Icons.paths[root.name] || "" }
        }
    }
}
