pragma Singleton
import QtQuick 2.15

// Calm dark palette (shadcn/zinc-like), one accent per status. Depth comes from real
// shadows and blur, not from colour.
QtObject {
    readonly property color bgTop: "#15151C"
    readonly property color bgBottom: "#07070A"
    readonly property color surface: "#18181F"
    readonly property color surfaceHi: "#22222B"
    readonly property color hairline: Qt.rgba(1, 1, 1, 0.07)
    readonly property color text: "#F4F4F5"
    readonly property color muted: "#A1A1AA"
    readonly property color subtle: "#71717A"
    readonly property color primary: "#FAFAFA"
    readonly property color primaryText: "#09090B"
    readonly property color success: "#10B981"
    readonly property color warning: "#F59E0B"
    readonly property color danger: "#EF4444"
    readonly property string font: "Inter"
}
