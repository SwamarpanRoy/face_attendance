import QtQuick 2.15
import QtQuick.Layouts 1.15
import QtGraphicalEffects 1.15
import Device 1.0

// Enrolment: pick the student -> consent -> 3 captures -> result.
Item {
    id: root
    readonly property string page: device.enrolPage

    // ------------------------------------------------------------ pick
    ColumnLayout {
        visible: root.page === "pick"
        anchors.fill: parent
        anchors.margins: 24
        spacing: 0
        RowLayout {
            Layout.fillWidth: true
            Text { text: "Enrol a student"; color: Theme.text; font.family: Theme.font
                   font.pixelSize: 26; font.weight: Font.Bold; Layout.fillWidth: true }
            Button { variant: "ghost"; text: "Close"; implicitHeight: 44
                     onClicked: device.enrolClose() }
        }
        Flow {  // sections as chips
            Layout.fillWidth: true
            Layout.topMargin: 12
            spacing: 8
            Repeater {
                model: device.enrolSections
                Rectangle {
                    readonly property bool chosen: index === device.enrolSectionIndex
                    width: chip.implicitWidth + 28; height: 38; radius: 19
                    color: chosen ? Theme.primary : Theme.surface
                    border.width: chosen ? 0 : 1
                    border.color: Theme.hairline
                    Text { id: chip; anchors.centerIn: parent; text: modelData
                           color: parent.chosen ? Theme.primaryText : Theme.text
                           font.family: Theme.font; font.pixelSize: 14; font.weight: Font.DemiBold }
                    MouseArea { anchors.fill: parent; onClicked: device.enrolSelectSection(index) }
                }
            }
        }
        Text {
            text: device.enrolSections.length === 0
                  ? "No sections synced yet. Connect to WiFi and use Sync now."
                  : device.enrolDigits === "" ? "Tap a name, or type the last digits of the USN."
                  : "USN ending in " + device.enrolDigits
            color: Theme.muted
            font.family: Theme.font
            font.pixelSize: 14
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
            Layout.topMargin: 12
        }
        ListView {
            id: roster
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 10
            clip: true
            spacing: 8
            model: device.enrolRoster
            boundsBehavior: Flickable.StopAtBounds
            delegate: Rectangle {
                width: roster.width
                height: 60
                radius: 18
                color: Theme.surface
                border.color: Theme.hairline
                scale: pick.pressed ? 0.98 : 1
                Behavior on scale { NumberAnimation { duration: 110 } }
                Column {
                    anchors { left: parent.left; leftMargin: 18; right: enrolled.left
                              rightMargin: 10; verticalCenter: parent.verticalCenter }
                    Text { width: parent.width; text: modelData.name; color: Theme.text
                           font.family: Theme.font; font.pixelSize: 16; font.weight: Font.DemiBold
                           elide: Text.ElideRight }
                    Text { text: modelData.usn; color: Theme.subtle; font.family: Theme.font
                           font.pixelSize: 12 }
                }
                Pill {
                    id: enrolled
                    visible: modelData.enrolled > 0
                    anchors { right: parent.right; rightMargin: 12; verticalCenter: parent.verticalCenter }
                    implicitHeight: 28
                    text: "Enrolled ×" + modelData.enrolled
                    tone: "ok"
                    dot: false
                }
                MouseArea { id: pick; anchors.fill: parent; onClicked: device.enrolChoose(modelData.usn) }
            }
        }
        Keypad {
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 12
            key: 56
            showConfirm: false
            onDigit: device.enrolKey(value)
            onBackspace: device.enrolBackspace()
        }
    }

    // ------------------------------------------------------------ consent
    ColumnLayout {
        visible: root.page === "consent"
        anchors.fill: parent
        anchors.margins: 24
        spacing: 0
        Text { text: "CONSENT"; color: Theme.subtle; font.family: Theme.font; font.pixelSize: 12
               font.weight: Font.DemiBold; font.letterSpacing: 1 }
        Text { text: device.enrolConsentTitle; color: Theme.text; font.family: Theme.font
               font.pixelSize: 24; font.weight: Font.Bold; wrapMode: Text.WordWrap
               Layout.fillWidth: true; Layout.topMargin: 6 }
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 16
            radius: 22
            color: Theme.surface
            border.color: Theme.hairline
            Flickable {
                id: notice
                anchors.fill: parent
                anchors.margins: 18
                clip: true
                contentHeight: noticeText.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                Text { id: noticeText; width: notice.width; text: device.enrolNotice
                       color: Theme.text; font.family: Theme.font; font.pixelSize: 16
                       lineHeight: 1.3; wrapMode: Text.WordWrap }
            }
        }
        Text { text: "Consent version " + device.enrolVersion; color: Theme.subtle
               font.family: Theme.font; font.pixelSize: 12; Layout.topMargin: 10 }
        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 14
            spacing: 12
            Button { Layout.preferredWidth: 140; text: "Decline"; onClicked: device.enrolBack() }
            Button { Layout.fillWidth: true; variant: "primary"; text: "I consent"; icon: "check"
                     onClicked: device.enrolConsent() }
        }
    }

    // ------------------------------------------------------------ capture
    ColumnLayout {
        visible: root.page === "capture"
        anchors.fill: parent
        anchors.margins: 24
        spacing: 0
        Text { text: device.enrolCaptureTitle; color: Theme.text; font.family: Theme.font
               font.pixelSize: 26; font.weight: Font.Bold; Layout.alignment: Qt.AlignHCenter }
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 16
            VideoSurface {
                id: capVideo
                anchors.fill: parent
                sink: device.videoSink
                visible: false
            }
            Rectangle { id: capMask; anchors.fill: parent; radius: 26; visible: false }
            OpacityMask { anchors.fill: parent; source: capVideo; maskSource: capMask }
            Text {
                anchors.centerIn: parent
                visible: device.videoSink.message !== ""
                text: device.videoSink.message
                color: Theme.muted
                font.family: Theme.font
                font.pixelSize: 16
            }
        }
        Row {  // progress: one bar per capture
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 18
            spacing: 8
            Repeater {
                model: device.enrolRequired
                Rectangle {
                    width: 64; height: 6; radius: 3
                    color: index < device.enrolCaptured ? Theme.success : Qt.rgba(1, 1, 1, 0.14)
                    Behavior on color { ColorAnimation { duration: 200 } }
                }
            }
        }
        Text { text: device.enrolCaptureStatus; color: Theme.muted; font.family: Theme.font
               font.pixelSize: 15; Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 10 }
        Button { Layout.fillWidth: true; Layout.topMargin: 14; text: "Cancel"
                 onClicked: device.enrolClose() }
    }

    // ------------------------------------------------------------ done
    ColumnLayout {
        id: done
        visible: root.page === "done"
        anchors.fill: parent
        anchors.margins: 24
        spacing: 0
        readonly property bool ok: !device.enrolCanRetry
        Item { Layout.fillHeight: true }
        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            width: 88; height: 88; radius: 44
            color: done.ok ? Theme.success : Theme.danger
            layer.enabled: true
            layer.effect: Glow { radius: 20; samples: 41; spread: 0.2
                                 color: done.ok ? "#8010B981" : "#80EF4444" }
            Icon { anchors.centerIn: parent; size: 44; color: "#ffffff"
                   name: done.ok ? "check" : "x" }
        }
        Text { text: device.enrolDoneTitle; color: Theme.text; font.family: Theme.font
               font.pixelSize: 28; font.weight: Font.Bold; Layout.alignment: Qt.AlignHCenter
               Layout.topMargin: 22 }
        Text { text: device.enrolDoneText; color: Theme.muted; font.family: Theme.font
               font.pixelSize: 15; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
               Layout.fillWidth: true; Layout.topMargin: 10 }
        Item { Layout.fillHeight: true }
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Button { visible: device.enrolCanRetry; Layout.fillWidth: true; text: "Try again"
                     icon: "rotate-ccw"; onClicked: device.enrolRetry() }
            Button { Layout.fillWidth: true; variant: "primary"; text: "Done"
                     onClicked: device.enrolClose() }
        }
    }
}
