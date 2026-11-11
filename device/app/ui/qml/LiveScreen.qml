import QtQuick 2.15
import QtGraphicalEffects 1.15
import Device 1.0

// Camera-first: full-bleed video; a frosted top bar (what is running, how many are in)
// and a frosted result card that turns green / amber / red. Glass panels are direct
// children of this item, so their coordinates match the video's (Glass blurs that rect).
Item {
    id: root
    readonly property string tone: device.bannerTone
    readonly property bool loud: tone !== "neutral"
    readonly property bool isSession: device.endLabel === "End session"

    VideoSurface { id: video; anchors.fill: parent; sink: device.videoSink }
    Text {
        anchors.centerIn: parent
        width: parent.width - 64
        visible: device.videoSink.message !== ""
        text: device.videoSink.message
        color: Theme.muted
        font.family: Theme.font
        font.pixelSize: 16
        wrapMode: Text.WordWrap
        horizontalAlignment: Text.AlignHCenter
    }

    Rectangle {  // scrim so the bottom cards read on any background
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: 400
        gradient: Gradient {
            GradientStop { position: 0.0; color: "transparent" }
            GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, 0.72) }
        }
    }

    Glass {
        id: topBar
        backdrop: video
        x: 16; y: 16; width: parent.width - 32; height: 72
        radius: 24
        Icon { name: "scan-face"; size: 26
               anchors { left: parent.left; leftMargin: 18; verticalCenter: parent.verticalCenter } }
        Column {
            anchors { left: parent.left; leftMargin: 58; right: counter.left; rightMargin: 10
                      verticalCenter: parent.verticalCenter }
            spacing: 2
            Text { width: parent.width; text: device.liveTitle; color: Theme.text
                   font.family: Theme.font; font.pixelSize: 16; font.weight: Font.DemiBold
                   elide: Text.ElideRight }
            Text { width: parent.width; text: device.liveSubtitle; color: Qt.rgba(1, 1, 1, 0.7)
                   font.family: Theme.font; font.pixelSize: 13; elide: Text.ElideRight }
        }
        Rectangle {
            id: counter
            visible: root.isSession
            anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
            width: Math.max(76, count.implicitWidth + 24); height: 48; radius: 16
            color: Qt.rgba(1, 1, 1, 0.10)
            Column {
                anchors.centerIn: parent
                Text { id: count; text: device.presentCount + "/" + device.totalCount
                       color: Theme.text; font.family: Theme.font; font.pixelSize: 18
                       font.weight: Font.Bold; anchors.horizontalCenter: parent.horizontalCenter }
                Text { text: "present"; color: Qt.rgba(1, 1, 1, 0.65); font.family: Theme.font
                       font.pixelSize: 11; anchors.horizontalCenter: parent.horizontalCenter }
            }
        }
    }

    Pill {
        visible: !device.liveCalibrated
        anchors { top: topBar.bottom; topMargin: 10; horizontalCenter: parent.horizontalCenter }
        text: "Not calibrated"
        tone: "warn"
        dot: false
    }

    Column {  // recent marks + pipeline timings, above the card
        x: 24; width: parent.width - 48
        anchors.bottom: card.top
        anchors.bottomMargin: 10
        spacing: 4
        Text { width: parent.width; text: device.recentText; visible: text !== ""
               color: Qt.rgba(1, 1, 1, 0.7); font.family: Theme.font; font.pixelSize: 13
               elide: Text.ElideRight }
        Text { width: parent.width; text: device.stats; visible: text !== ""
               color: Qt.rgba(1, 1, 1, 0.4); font.family: Theme.font; font.pixelSize: 10
               wrapMode: Text.WordWrap }
    }

    Glass {
        id: card
        backdrop: video
        x: 16; width: parent.width - 32
        height: Math.max(88, cardText.implicitHeight + 36)
        y: endButton.y - 12 - height
        radius: 26
        visible: device.bannerText !== ""
        tint: root.tone === "ok" ? Qt.rgba(0.06, 0.73, 0.51, 0.34)
            : root.tone === "warn" ? Qt.rgba(0.96, 0.62, 0.04, 0.32)
            : root.tone === "error" ? Qt.rgba(0.94, 0.27, 0.27, 0.32)
            : Qt.rgba(0.09, 0.09, 0.11, 0.45)
        edge: root.tone === "ok" ? Qt.rgba(0.43, 0.91, 0.72, 0.55)
            : root.tone === "warn" ? Qt.rgba(0.99, 0.83, 0.30, 0.55)
            : root.tone === "error" ? Qt.rgba(0.99, 0.65, 0.65, 0.55)
            : Qt.rgba(1, 1, 1, 0.16)
        transform: Scale { id: pop; origin.x: card.width / 2; origin.y: card.height / 2 }
        Rectangle {
            id: badge
            anchors { left: parent.left; leftMargin: 16; verticalCenter: parent.verticalCenter }
            width: 56; height: 56; radius: 28
            color: root.tone === "ok" ? Theme.success : root.tone === "warn" ? Theme.warning
                 : root.tone === "error" ? Theme.danger : Qt.rgba(1, 1, 1, 0.10)
            layer.enabled: root.loud
            layer.effect: Glow { radius: 16; samples: 33; spread: 0.2
                                 color: Qt.rgba(badge.color.r, badge.color.g, badge.color.b, 0.5) }
            Icon {
                anchors.centerIn: parent
                size: 28
                color: "#ffffff"
                name: root.tone === "ok" || root.tone === "warn" ? "check"
                    : root.tone === "error" ? "x" : "scan-face"
            }
        }
        Column {
            id: cardText
            anchors { left: badge.right; leftMargin: 16; right: parent.right; rightMargin: 18
                      verticalCenter: parent.verticalCenter }
            spacing: 4
            Text {
                width: parent.width
                text: device.bannerTitle
                color: "#ffffff"
                font.family: Theme.font
                font.pixelSize: root.loud ? 24 : 18
                font.weight: root.loud ? Font.Bold : Font.DemiBold
                wrapMode: Text.WordWrap
                maximumLineCount: 2
                elide: Text.ElideRight
            }
            Text {
                width: parent.width
                visible: text !== ""
                text: device.bannerSubtitle
                color: Qt.rgba(1, 1, 1, 0.8)
                font.family: Theme.font
                font.pixelSize: 14
                elide: Text.ElideRight
            }
        }
    }
    SequentialAnimation {  // a small pop whenever a result (not a hint) arrives
        id: popIn
        NumberAnimation { target: pop; properties: "xScale,yScale"; from: 0.94; to: 1.03
                          duration: 140; easing.type: Easing.OutCubic }
        NumberAnimation { target: pop; properties: "xScale,yScale"; to: 1.0; duration: 120 }
    }
    Connections {
        target: device
        function onLiveChanged() {
            if (root.loud && device.bannerText !== root.lastBanner) popIn.restart()
            root.lastBanner = device.bannerText
        }
    }
    property string lastBanner: ""

    Row {
        id: endButton
        x: 16; width: parent.width - 32; height: 64
        y: parent.height - 16 - height
        spacing: 12
        Button {
            visible: device.captureVisible
            width: (parent.width - 12) / 2
            height: parent.height
            text: "Capture test"
            icon: "camera"
            onClicked: device.captureTest()
        }
        Glass {
            id: endGlass
            backdrop: video
            // Glass blurs its own rect in `video` coordinates, so it needs its real position.
            x: 0
            width: device.captureVisible ? (parent.width - 12) / 2 : parent.width
            height: parent.height
            radius: 22
            tint: root.isSession ? Qt.rgba(0.94, 0.27, 0.27, 0.18) : Qt.rgba(1, 1, 1, 0.10)
            edge: root.isSession ? Qt.rgba(0.99, 0.65, 0.65, 0.35) : Qt.rgba(1, 1, 1, 0.18)
            scale: endArea.pressed ? 0.97 : 1
            Behavior on scale { NumberAnimation { duration: 120 } }
            Row {
                anchors.centerIn: parent
                spacing: 10
                Icon { name: root.isSession ? "circle-stop" : "x"; size: 22
                       color: root.isSession ? "#FCA5A5" : Theme.text
                       anchors.verticalCenter: parent.verticalCenter }
                Text { text: device.endLabel; color: root.isSession ? "#FCA5A5" : Theme.text
                       font.family: Theme.font; font.pixelSize: 18; font.weight: Font.DemiBold
                       anchors.verticalCenter: parent.verticalCenter }
            }
            MouseArea { id: endArea; anchors.fill: parent; onClicked: device.endSession() }
        }
    }
}
