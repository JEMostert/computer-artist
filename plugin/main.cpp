// SPDX-License-Identifier: GPL-2.0-or-later
// Compiled against INSTALLED KWin headers; no fork symbols or memory patching.
#include <plugin.h>
#include <main.h>
#include <workspace.h>
#include <window.h>
#include <input.h>
#include <input_event_spy.h>
#include <input_event.h>
#include <core/inputdevice.h>
#include <pointer_input.h>
#include <keyboard_input.h>
#include <xkb.h>
#include "clipboard.h"
#include <wayland_server.h>
#include <wayland/clientconnection.h>
#include <wayland/display.h>
#include <wayland/surface.h>
#include <wayland/seat.h>
#include <wayland/pointer.h>
#include <scene/imageitem.h>
#include <scene/workspacescene.h>
#include <core/graphicsbufferview.h>
#include <wayland-server-core.h>
#include <wayland-server-protocol.h>
#include <QLocalServer>
#include <QLocalSocket>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QDir>
#include <QFileInfo>
#include <QElapsedTimer>
#include <QTimer>
#include <QPointer>
#include <QPainter>
#include <QPainterPath>
#include <QUuid>
#include <QSet>
#include <QSaveFile>
#include <algorithm>
#include <cmath>
#include <numbers>
#include <functional>

namespace KWin {
// This distinct source identity lets real-device events preempt host automation.
class HostDevice : public InputDevice {
public:
    QString name() const override { return QStringLiteral("Computer Artist host input"); }
    bool isEnabled() const override { return true; }
    void setEnabled(bool) override {}
    bool isKeyboard() const override { return true; }
    bool isPointer() const override { return true; }
    bool isTouchpad() const override { return false; }
    bool isTouch() const override { return false; }
    bool isTabletTool() const override { return false; }
    bool isTabletPad() const override { return false; }
    bool isTabletModeSwitch() const override { return false; }
    bool isLidSwitch() const override { return false; }
};

class Artist : public Plugin, public InputEventSpy {
    Q_OBJECT
public:
    Artist();
    ~Artist() override;
    bool start();
    void pointerMotion(PointerMotionEvent *event) override;
    void pointerButton(PointerButtonEvent *event) override;
    void pointerAxis(PointerAxisEvent *event) override;
    void keyboardKey(KeyboardKeyEvent *event) override;
    void touchDown(TouchDownEvent *) override { releaseHost(true); }
    void tabletToolProximityEvent(TabletToolProximityEvent *) override { releaseHost(true); }

private:
    void ensureSession();
    bool hostValid() const;
    bool keyboardReady() const;
    bool acquireHost(Window *, QLocalSocket *);
    void releaseHost(bool revoke = false, uint32_t transferredButton = 0, uint32_t transferredKey = 0);
    QJsonObject requestHost(QLocalSocket *, const QJsonObject &);
    std::chrono::microseconds hostTime() const { return std::chrono::microseconds(m_clock.nsecsElapsed()/1000); }
    HostDevice m_hostDevice;
    QPointer<QLocalSocket> m_hostOwner;
    QPointer<Window> m_hostTarget;
    QPointer<ClientConnection> m_hostClient;
    QString m_hostLease;
    quint64 m_hostGeneration = 0;
    QSet<uint32_t> m_hostButtons;
    QList<uint32_t> m_hostKeys;
    bool m_hostKeyboardUsed = false;
    Clipboard *m_clipboard = nullptr;
    QTimer m_hostWatchdog;
    bool m_releasingHost = false;
    QString m_hostStopReason;
    QJsonObject request(QLocalSocket *, const QJsonObject &);
    QJsonArray windows() const;
    Window *find(const QString &id) const;
    bool visible(Window *) const;
    Window *at(const QPointF &) const;
    bool owns(SurfaceInterface *s) const { return m_client && s && s->client() == m_client; }
    QList<uint32_t> pointers() const;
    void send(const std::function<void(wl_resource *)> &);
    void frame();
    bool acquire(Window *, QLocalSocket *);
    bool move(const QPointF &);
    bool button(uint32_t, bool);
    void cancel();
    void release(bool revoke = false);
    void drawCursor();
    void observe(Window *);
    bool valid() const;
    QLocalServer m_server;
    QPointer<ClientConnection> m_client;
    QPointer<Window> m_target;
    QPointer<SurfaceInterface> m_surface;
    QPointer<QLocalSocket> m_owner;
    QList<uint32_t> m_resources;
    QSet<uint32_t> m_buttons;
    QString m_lease;
    QString m_session;
    quint64 m_generation = 0;
    QMetaObject::Connection m_clientClosed;
    QTimer m_watchdog;
    QTimer m_animation;
    QPointer<ImageItem> m_cursor;
    QPointF m_position;
    QElapsedTimer m_clock;
    QList<std::pair<qint64,QPointF>> m_trail;
    qint64 m_lastMove = -1000, m_lastClick = -1000;
    qreal m_lean = 0;
    bool m_positionKnown = false;
};

Artist::Artist() {
    m_clock.start();
    input()->addInputDevice(&m_hostDevice);
    input()->installInputEventSpy(this);
    m_hostWatchdog.setSingleShot(true);
    m_hostWatchdog.setInterval(5000);
    connect(&m_hostWatchdog, &QTimer::timeout, this, [this] { m_hostStopReason="watchdog"; releaseHost(true); });
    m_watchdog.setSingleShot(true);
    m_watchdog.setInterval(5000);
    connect(&m_watchdog, &QTimer::timeout, this, [this] { release(true); });
    connect(&m_animation, &QTimer::timeout, this, [this] {
        if (m_client && !valid()) release(true);
        if (m_hostOwner && !hostValid()) { m_hostStopReason="target_unavailable"; releaseHost(true); }
        if (m_positionKnown) drawCursor();
    });
    // This signal runs before PointerInputRedirection::update() sends human enter.
    connect(input(), &InputRedirection::globalPointerChanged, this, [this](const QPointF &p) {
        auto w = at(p);
        if (w && owns(w->surface())) release(true);
    });
    connect(waylandServer()->seat(), &SeatInterface::focusedKeyboardSurfaceAboutToChange,
            this, [this](SurfaceInterface *s) {
        if (owns(s)) release(true);
        if (m_hostOwner && m_hostKeyboardUsed && (!m_hostTarget || s!=m_hostTarget->surface())) {
            m_hostStopReason="keyboard_focus_changed"; releaseHost(true);
        }
    });
    // Catch stationary-pointer focus changes from stacking/geometry, too. A human
    // enter may already have been sent here; release must preserve that enter.
    connect(waylandServer()->seat()->pointer(), &PointerInterface::focusedSurfaceChanged,
            this, [this] { if (owns(waylandServer()->seat()->focusedPointerSurface())) release(true); });
    for (auto w : workspace()->stackingOrder()) observe(w);
    connect(workspace(), &Workspace::windowAdded, this, [this](Window *w) {
        observe(w);
        if (w->surface() && owns(w->surface())) release(true);
        if (m_hostClient && w->surface() && w->surface()->client()==m_hostClient) {
            m_hostStopReason="new_application_window"; releaseHost(true);
        }
    });
}
Artist::~Artist() {
    m_animation.stop();
    release(true);
    releaseHost(true);
    input()->uninstallInputEventSpy(this);
    input()->removeInputDevice(&m_hostDevice);
    delete m_cursor.data();
}
bool Artist::start() {
    const auto displays=waylandServer()->display()->socketNames();
    if(!displays.isEmpty()) m_clipboard=new Clipboard(displays.first(),this);
    QString dir = qEnvironmentVariable("CA_PLUGIN_RUNTIME");
    if (dir.isEmpty()) dir = qEnvironmentVariable("XDG_RUNTIME_DIR") + "/computer-artist";
    if (!QDir().mkpath(dir)) return false;
    QFile::setPermissions(dir, QFile::ReadOwner | QFile::WriteOwner | QFile::ExeOwner);
    const auto socketPath = dir + "/control";
    // Only remove a stale socket after checking whether another server owns it.
    if (QFileInfo::exists(socketPath)) {
        QLocalSocket probe;
        probe.connectToServer(socketPath);
        if (probe.waitForConnected(100)) return false;
        QLocalServer::removeServer(socketPath);
    }
    m_server.setSocketOptions(QLocalServer::UserAccessOption);
    if (!m_server.listen(socketPath)) return false;
    connect(&m_server, &QLocalServer::newConnection, this, [this] {
        while (auto socket = m_server.nextPendingConnection()) {
            socket->setParent(this);
            socket->setReadBufferSize(65537);
            connect(socket, &QLocalSocket::disconnected, this, [this,socket] {
                if (m_owner == socket) release();
                if (m_hostOwner == socket) { m_hostStopReason="disconnect"; releaseHost(); }
                socket->deleteLater();
            });
            connect(socket, &QLocalSocket::readyRead, this, [this,socket] {
                while (socket->canReadLine() && !socket->property("revoked").toBool()) {
                    if(socket->property("clipboard_pending").toBool()) { socket->abort(); return; }
                    auto line = socket->readLine(65537);
                    if (line.size() > 65536) { socket->abort(); return; }
                    auto doc = QJsonDocument::fromJson(line);
                    auto reply = doc.isObject() ? request(socket, doc.object()) : QJsonObject{{"ok",false},{"error","invalid_json"}};
                    if(!reply.value("pending").toBool()) socket->write(QJsonDocument(reply).toJson(QJsonDocument::Compact) + '\n');
                    if (socket->bytesToWrite() > 1024*1024) { socket->abort(); return; }
                }
                if (socket->bytesAvailable() > 65536) socket->abort();
            });
        }
    });
    return true;
}
bool Artist::visible(Window *w) const {
    return w && !w->isDeleted() && !w->isMinimized() && !w->isHidden()
        && !w->isHiddenByShowDesktop() && w->isOnCurrentDesktop()
        && w->isOnCurrentActivity() && w->readyForPainting();
}
Window *Artist::find(const QString &id) const {
    for (auto w : workspace()->stackingOrder()) if (w->internalId() == QUuid(id)) return w;
    return nullptr;
}
Window *Artist::at(const QPointF &p) const {
    const auto order = workspace()->stackingOrder();
    for (auto it=order.crbegin(); it!=order.crend(); ++it) if (visible(*it) && (*it)->hitTest(p)) return *it;
    return nullptr;
}
QList<uint32_t> Artist::pointers() const {
    QList<uint32_t> result;
    if (!m_client || m_client->tearingDown()) return result;
    wl_client_for_each_resource(m_client->client(), [](wl_resource *r, void *data) {
        if (strcmp(wl_resource_get_class(r), "wl_pointer") == 0)
            static_cast<QList<uint32_t> *>(data)->append(wl_resource_get_id(r));
        return WL_ITERATOR_CONTINUE;
    }, &result);
    std::sort(result.begin(),result.end());
    return result;
}
void Artist::send(const std::function<void(wl_resource *)> &f) {
    if (!m_client || m_client->tearingDown()) return;
    for (auto id : m_resources) {
        auto r = wl_client_get_object(m_client->client(), id);
        if (r && strcmp(wl_resource_get_class(r),"wl_pointer") == 0) f(r);
    }
    m_client->flush();
}
void Artist::frame() {
    send([](wl_resource *r) { if (wl_resource_get_version(r) >= WL_POINTER_FRAME_SINCE_VERSION) wl_pointer_send_frame(r); });
}
bool Artist::valid() const {
    if (!m_client || !visible(m_target) || waylandServer()->isScreenLocked()) return false;
    auto seat = waylandServer()->seat();
    if (owns(seat->focusedPointerSurface()) || owns(seat->focusedKeyboardSurface()) || seat->isDrag() || seat->isTouchSequence()) return false;
    for (auto w : workspace()->stackingOrder()) if (owns(w->surface()) && (w->hasPopupGrab() || w->isSpecialWindow())) return false;
    return true;
}
void Artist::observe(Window *w) {
    // Release before KWin's windowActivated handler snapshots held keys for
    // the next keyboard enter event.
    connect(w,&Window::activeChanged,this,[this,w] {
        if(m_hostTarget==w && m_hostKeyboardUsed && !w->isActive()) {
            m_hostStopReason="keyboard_focus_changed"; releaseHost(true);
        }
    });
    auto changed = [this,w] {
        if (m_client && owns(w->surface())) { ++m_generation; release(true); }
        if (m_hostTarget == w) { ++m_hostGeneration; m_hostStopReason="target_changed"; releaseHost(true); }
    };
    connect(w, &Window::closed, this, changed);
    connect(w, &Window::frameGeometryChanged, this, changed);
    connect(w, &Window::minimizedChanged, this, changed);
    connect(w, &Window::hiddenChanged, this, changed);
}
bool Artist::acquire(Window *w, QLocalSocket *socket) {
    Qt::MouseButtons syntheticHostButtons=Qt::NoButton;
    if(m_hostButtons.contains(272)) syntheticHostButtons|=Qt::LeftButton;
    if(m_hostButtons.contains(273)) syntheticHostButtons|=Qt::RightButton;
    if(m_hostButtons.contains(274)) syntheticHostButtons|=Qt::MiddleButton;
    if (m_client || !visible(w) || !w->inherits("KWin::XdgToplevelWindow") || w->isSpecialWindow()
        || !w->surface() || w->surface()->client() == waylandServer()->xWaylandConnection()
        || (input()->pointer()->buttons() & ~syntheticHostButtons) != Qt::NoButton || !input()->keyboard()->pressedKeys().isEmpty()) return false;
    if (m_hostClient && w->surface()->client()==m_hostClient) return false;
    m_client = w->surface()->client();
    m_target = w;
    if (!valid()) { m_client.clear(); m_target.clear(); return false; }
    m_resources = pointers();
    if (m_resources.isEmpty()) { m_client.clear(); m_target.clear(); return false; }
    m_clientClosed = connect(m_client, &ClientConnection::aboutToBeDestroyed, this, [this] { release(true); });
    m_owner = socket;
    m_lease = QUuid::createUuid().toString(QUuid::WithoutBraces);
    ++m_generation;
    m_watchdog.start();
    ensureSession();
    return true;
}
bool Artist::move(const QPointF &p) {
    if (!valid() || !std::isfinite(p.x()) || !std::isfinite(p.y()) || pointers() != m_resources) return false;
    auto w = at(p);
    if (!w || w != m_target || !w->clientGeometry().contains(p)) return false;
    auto [surface, local] = w->surface()->mapToInputSurface(w->inputTransformation().map(p));
    if (!surface || surface->lockedPointer() || surface->confinedPointer()) return false;
    local = surface->toSurfaceLocal(local);
    if (m_surface != surface) {
        if (!m_buttons.isEmpty()) return false;
        cancel();
        m_surface = surface;
        const auto serial = waylandServer()->display()->nextSerial();
        send([&](wl_resource *r) { wl_pointer_send_enter(r, serial, surface->resource(), wl_fixed_from_double(local.x()), wl_fixed_from_double(local.y())); });
    } else {
        send([&](wl_resource *r) { wl_pointer_send_motion(r, uint32_t(m_clock.elapsed()), wl_fixed_from_double(local.x()), wl_fixed_from_double(local.y())); });
    }
    frame();
    m_positionKnown = true;
    m_position = p;
    m_trail.append({m_clock.elapsed(),p});
    m_lastMove = m_clock.elapsed();
    drawCursor();
    return true;
}
bool Artist::button(uint32_t code, bool down) {
    if (!valid() || !m_surface || pointers()!=m_resources || at(m_position)!=m_target
        || (code < 272 || code > 274) || m_buttons.contains(code)==down) return false;
    if (down) { m_buttons.insert(code); m_lastClick=m_clock.elapsed(); } else m_buttons.remove(code);
    auto serial = waylandServer()->display()->nextSerial();
    send([&](wl_resource *r) { wl_pointer_send_button(r, serial, uint32_t(m_clock.elapsed()), code, down ? WL_POINTER_BUTTON_STATE_PRESSED : WL_POINTER_BUTTON_STATE_RELEASED); });
    frame();
    return true;
}
void Artist::cancel() {
    for (auto code : std::as_const(m_buttons)) {
        auto serial = waylandServer()->display()->nextSerial();
        send([&](wl_resource *r) { wl_pointer_send_button(r, serial, uint32_t(m_clock.elapsed()), code, WL_POINTER_BUTTON_STATE_RELEASED); });
    }
    m_buttons.clear();
    // Never erase a new human enter that stock KWin has already delivered.
    if (m_surface && !owns(waylandServer()->seat()->focusedPointerSurface())) {
        auto serial = waylandServer()->display()->nextSerial();
        send([&](wl_resource *r) { wl_pointer_send_leave(r, serial, m_surface->resource()); });
    }
    frame();
    m_surface.clear();
}
void Artist::release(bool revoke) {
    auto owner = m_owner;
    m_owner.clear();
    m_watchdog.stop();
    cancel();
    disconnect(m_clientClosed);
    m_client.clear(); m_target.clear(); m_resources.clear(); m_lease.clear();
    if (revoke && owner) { owner->setProperty("revoked",true); owner->abort(); }
}
QJsonArray Artist::windows() const {
    QJsonArray result;
    for (auto w : workspace()->stackingOrder()) {
        if (w->isDeleted() || !w->surface()) continue;
        auto rect = w->clientGeometry();
        const bool native = w->surface()->client()!=waylandServer()->xWaylandConnection();
        result.append(QJsonObject{{"id",w->internalId().toString(QUuid::WithoutBraces)}, {"title",w->caption()},
            {"pid",int(w->surface()->client()->processId())},{"native",native},{"backend",native?"wayland":"xwayland"},
            {"visible",visible(w)},{"agent",owns(w->surface())},{"host",m_hostClient && w->surface()->client()==m_hostClient},{"human_active",workspace()->activeWindow()==w},
            {"acquirable",native && !w->isSpecialWindow() && !owns(w->surface())},
            {"x",rect.x()},{"y",rect.y()},{"width",rect.width()},{"height",rect.height()}});
    }
    return result;
}
QJsonObject Artist::request(QLocalSocket *socket, const QJsonObject &o) {
    const auto op = o.value("op").toString();
    const auto lane = o.value("lane").toString("agent");
    if (lane!="agent" && lane!="host") return {{"ok",false},{"error","invalid_lane"}};
    const bool observation = op=="windows" || op=="capabilities" || op=="ping" || op=="capture";
    if (lane=="host" && !observation && op!="session_status" && op!="session_close") return requestHost(socket,o);
    QJsonObject reply;
    bool ok=false;
    if (!observation && op!="takeover" && op!="session_close" && op!="session_status" && lane=="agent" && m_owner && m_owner!=socket) reply.insert("error","controller_busy");
    else if (op=="capabilities") {
        reply = {{"protocol",3},{"backend","stock_kwin_plugin"},{"ownership","wayland_connection_pointer"},
            {"automatic_sessions",true},{"lane",lane},{"host_pointer",true},{"host_focus",true},{"host_keyboard",true},{"host_xwayland",false},{"native_handoff",true},{"xwayland_handoff",false},{"keyboard",false},{"input_methods",false},
            {"clipboard",false},{"data_drag_and_drop",false},{"popups",false},{"human_pointer_entry_takeover",true},
            {"operations",QJsonArray{"session_close","session_status","windows","capabilities","acquire","release","move","button","scroll","cancel","takeover","ping","capture"}}};
        if (lane=="host") {
            reply.insert("ownership","host_wayland_connection");
            reply.insert("keyboard",true);
            const bool clipboard=m_clipboard && m_clipboard->ready();
            reply.insert("clipboard",clipboard);
            reply.insert("clipboard_max_bytes",Clipboard::MaxBytes);
            auto operations=QJsonArray{"session_close","session_status","windows","capabilities","acquire","release","move","button","scroll","focus","key","cancel","takeover","ping","capture"};
            if(clipboard) { operations.append("clipboard_get"); operations.append("clipboard_set"); }
            reply.insert("operations",operations);
        }
        ok=true;
    } else if (op=="windows") { reply.insert("windows",windows()); ok=true; }
    else if (op=="session_status") ok=true;
    else if (op=="session_close") {
        // Session lifetime is explicit and independent of short-lived CLI sockets.
        release(m_owner != socket);
        m_hostStopReason="session_closed";
        releaseHost(m_hostOwner != socket);
        m_session.clear();
        m_animation.stop();
        m_positionKnown = false;
        m_trail.clear();
        m_lastMove = m_lastClick = -1000;
        m_lean = 0;
        if (m_cursor) m_cursor->setVisible(false);
        ok=true;
    }
    else if (op=="ping") ok=true;
    else if (op=="acquire") { ok=acquire(find(o.value("window").toString()),socket); if(!ok) reply.insert("error","unavailable_or_human_focus_or_busy"); }
    else if (op=="takeover") { release(true); ok=true; }
    else if (op=="release") { if (!m_client || (socket==m_owner && o.value("lease").toString()==m_lease)) { release(); ok=true; } }
    else if (op=="cancel") { if(socket==m_owner) { cancel(); ok=true; } }
    else if (op=="capture") {
        auto w=find(o.value("window").toString());
        const auto path=o.value("path").toString();
        if (!waylandServer()->isScreenLocked() && w && !w->isSpecialWindow() && w->surface() && w->surface()->buffer()
            && QFileInfo(path).isAbsolute() && !QFileInfo::exists(path)) {
            GraphicsBufferView view(w->surface()->buffer());
            if (!view.isNull()) {
                QSaveFile file(path);
                ok=file.open(QIODevice::WriteOnly) && view.image()->save(&file,"PNG") && file.commit();
                if(ok) reply.insert("path",path);
            }
        }
        if(!ok) reply.insert("error","capture_unavailable_or_path_exists");
    } else if (op=="move" || op=="button" || op=="scroll") {
        if (socket!=m_owner || !m_client || o.value("lease").toString()!=m_lease
            || o.value("generation").toInteger(-1)!=qint64(m_generation)) reply.insert("error","stale_lease_or_geometry");
        else if(op=="move" && o.value("x").isDouble() && o.value("y").isDouble()) ok=move({o.value("x").toDouble(),o.value("y").toDouble()});
        else if(op=="button" && o.value("code").isDouble() && o.value("pressed").isBool()) {
            const auto code=o.value("code").toDouble();
            if(code>=272 && code<=274 && code==std::floor(code)) ok=button(uint32_t(code),o.value("pressed").toBool());
        } else if(op=="scroll" && valid() && m_surface && pointers()==m_resources && at(m_position)==m_target
            && o.value("delta").isDouble() && (o.value("axis")=="vertical" || o.value("axis")=="horizontal")) {
            double delta=o.value("delta").toDouble();
            if(std::isfinite(delta) && std::abs(delta)<1000000) {
                const auto axis=o.value("axis")=="vertical" ? WL_POINTER_AXIS_VERTICAL_SCROLL : WL_POINTER_AXIS_HORIZONTAL_SCROLL;
                send([&](wl_resource *r) { wl_pointer_send_axis(r,uint32_t(m_clock.elapsed()),axis,wl_fixed_from_double(delta)); }); frame(); ok=true;
            }
        }
    }
    if (!ok) { if(socket==m_owner) cancel(); if(!reply.contains("error")) reply.insert("error","rejected_or_unsupported"); }
    if(socket==m_owner) m_watchdog.start();
    if(socket==m_hostOwner) m_hostWatchdog.start();
    reply.insert("host_position",QJsonArray{input()->pointer()->pos().x(),input()->pointer()->pos().y()});
    reply.insert("session",m_session);
    reply.insert("cursor_visible",m_cursor && m_cursor->isVisible());
    reply.insert("lanes",QJsonObject{
        {"agent",QJsonObject{{"busy",bool(m_owner)},{"window",m_target?m_target->internalId().toString(QUuid::WithoutBraces):QString()}, {"buttons",int(m_buttons.size())}}},
        {"host",QJsonObject{{"busy",bool(m_hostOwner)},{"window",m_hostTarget?m_hostTarget->internalId().toString(QUuid::WithoutBraces):QString()}, {"buttons",int(m_hostButtons.size())},{"keys",int(m_hostKeys.size())},{"stop_reason",m_hostStopReason}}}});
    reply.insert("ok",ok); reply.insert("lease",lane=="host"?m_hostLease:m_lease);
    reply.insert("generation",qint64(lane=="host"?m_hostGeneration:m_generation)); reply.insert("keyboard_ready",lane=="host" && keyboardReady());
    return reply;
}

void Artist::ensureSession() {
    if (m_session.isEmpty()) {
        m_session=QUuid::createUuid().toString(QUuid::WithoutBraces);
        m_animation.start(33);
    }
}
void Artist::pointerMotion(PointerMotionEvent *event) {
    if (m_hostOwner && event->device!=&m_hostDevice) {
        m_hostStopReason="external_pointer_motion"; releaseHost(true);
        event->buttons=input()->pointer()->buttons();
    }
}
void Artist::pointerButton(PointerButtonEvent *event) {
    if (m_hostOwner && event->device!=&m_hostDevice) {
        m_hostStopReason="external_pointer_button";
        // A physical press takes ownership of the same held button. Do not
        // synthesize an up that would erase the user's newly pressed button.
        releaseHost(true,event->state==PointerButtonState::Pressed?event->nativeButton:0);
        event->buttons=input()->pointer()->buttons();
    }
}
void Artist::pointerAxis(PointerAxisEvent *event) {
    if (m_hostOwner && event->device!=&m_hostDevice) { m_hostStopReason="external_scroll"; releaseHost(true); }
}
void Artist::keyboardKey(KeyboardKeyEvent *event) {
    // KWin generates repeats without a source device. They belong to our held
    // key until an actual external event transfers ownership.
    if(event->state==KeyboardKeyState::Repeated && m_hostKeys.contains(event->nativeScanCode)) return;
    if (m_hostOwner && event->device!=&m_hostDevice) {
        m_hostStopReason="external_keyboard";
        releaseHost(true,0,event->state==KeyboardKeyState::Pressed?event->nativeScanCode:0);
        // The event was translated before spies ran. Remove released synthetic
        // modifiers from the human event as well as KWin's keyboard state.
        auto xkb=input()->keyboard()->xkb();
        event->modifiers=xkb->modifiers();
        event->modifiersRelevantForGlobalShortcuts=xkb->modifiersRelevantForGlobalShortcuts(event->nativeScanCode);
        event->nativeVirtualKey=xkb->toKeysym(event->nativeScanCode);
        event->key=xkb->toQtKey(event->nativeVirtualKey,event->nativeScanCode);
        event->text=xkb->toString(event->nativeVirtualKey);
    }
}
bool Artist::keyboardReady() const {
    return m_hostOwner && hostValid() && workspace()->activeWindow()==m_hostTarget
        && waylandServer()->seat()->focusedKeyboardSurface()==m_hostTarget->surface();
}
bool Artist::hostValid() const {
    if(m_hostClient) for(auto w:workspace()->stackingOrder())
        if(w->surface() && w->surface()->client()==m_hostClient && (w->hasPopupGrab() || w->isSpecialWindow())) return false;
    return m_hostClient && visible(m_hostTarget) && !waylandServer()->isScreenLocked()
        && !waylandServer()->seat()->isDrag() && !waylandServer()->seat()->isTouchSequence()
        && !input()->pointer()->isConstrained()
        && m_hostTarget->surface() && m_hostTarget->surface()->client()==m_hostClient
        && !owns(m_hostTarget->surface());
}
bool Artist::acquireHost(Window *w, QLocalSocket *socket) {
    if (m_hostOwner || !visible(w) || !w->surface() || w->isSpecialWindow()
        || !w->inherits("KWin::XdgToplevelWindow") || owns(w->surface())
        || input()->pointer()->buttons()!=Qt::NoButton || !input()->keyboard()->pressedKeys().isEmpty()) return false;
    m_hostTarget=w; m_hostClient=w->surface()->client();
    if (!hostValid()) { m_hostTarget.clear(); m_hostClient.clear(); return false; }
    m_hostOwner=socket; m_hostLease=QUuid::createUuid().toString(QUuid::WithoutBraces);
    ++m_hostGeneration; m_hostStopReason.clear();
    m_hostWatchdog.start(); ensureSession();
    return true;
}
void Artist::releaseHost(bool revoke, uint32_t transferredButton, uint32_t transferredKey) {
    if (m_releasingHost) return;
    m_releasingHost=true;
    auto owner=m_hostOwner;
    m_hostOwner.clear(); m_hostWatchdog.stop();
    m_hostKeyboardUsed=false;
    auto keys=m_hostKeys; m_hostKeys.clear();
    for(auto i=keys.crbegin();i!=keys.crend();++i) {
        if(*i==transferredKey) {
            // XKB counted both the synthetic and physical press. Drop only our
            // contribution, keeping KWin's pressed-key entry and the client's
            // held key until the physical release arrives.
            input()->keyboard()->xkb()->updateKey(*i,KeyboardKeyState::Released);
        } else input()->keyboard()->processKey(*i,KeyboardKeyState::Released,hostTime(),&m_hostDevice);
    }
    auto buttons=m_hostButtons; m_hostButtons.clear();
    for (auto code : buttons) if(code!=transferredButton)
        input()->pointer()->processButton(code,PointerButtonState::Released,hostTime(),&m_hostDevice);
    if (!buttons.isEmpty()) input()->pointer()->processFrame(&m_hostDevice);
    m_hostTarget.clear(); m_hostClient.clear(); m_hostLease.clear();
    if (revoke && owner) { owner->setProperty("revoked",true); owner->abort(); }
    m_releasingHost=false;
}
QJsonObject Artist::requestHost(QLocalSocket *socket,const QJsonObject &o) {
    const auto op=o.value("op").toString();
    bool ok=false;
    QString error;
    if (op=="takeover") { m_hostStopReason="explicit_stop"; releaseHost(m_hostOwner!=socket); ok=true; }
    else if (m_hostOwner && m_hostOwner!=socket) error="host_controller_busy";
    else if (op=="clipboard_get" || op=="clipboard_set") {
        if(waylandServer()->isScreenLocked()) error="screen_locked";
        else if(!m_clipboard || !m_clipboard->ready()) error="clipboard_unavailable";
        else if(socket==m_hostOwner && (!hostValid() || o.value("lease").toString()!=m_hostLease
                || o.value("generation").toInteger(-1)!=qint64(m_hostGeneration))) error="stale_host_lease_or_target";
        else if(socket!=m_hostOwner && !o.value("lease").toString().isEmpty()) error="stale_host_lease_or_target";
        else if(op=="clipboard_set" && !o.value("text").isString()) error="clipboard_text_required";
        else {
            socket->setProperty("clipboard_pending",true);
            QPointer<QLocalSocket> receiver=socket;
            auto done=[receiver](QJsonObject result) {
                if(!receiver || receiver->state()!=QLocalSocket::ConnectedState) return;
                receiver->setProperty("clipboard_pending",false);
                result.insert("lane","host");
                receiver->write(QJsonDocument(result).toJson(QJsonDocument::Compact)+'\n');
            };
            if(op=="clipboard_get") m_clipboard->read(done);
            else m_clipboard->write(o.value("text").toString(),done);
            return {{"pending",true}};
        }
    }
    else if (op=="acquire") { ok=acquireHost(find(o.value("window").toString()),socket); if(!ok) error="host_target_unavailable_or_lane_conflict"; }
    else if(op=="release") {
        if(!m_hostOwner || (socket==m_hostOwner && o.value("lease").toString()==m_hostLease)) { releaseHost(); ok=true; }
    } else if(op=="cancel") { if(socket==m_hostOwner) { releaseHost(); ok=true; } }
    else if(socket!=m_hostOwner || !hostValid() || o.value("lease").toString()!=m_hostLease
            || o.value("generation").toInteger(-1)!=qint64(m_hostGeneration)) error="stale_host_lease_or_target";
    else if(op=="focus" && m_hostButtons.isEmpty() && m_hostKeys.isEmpty() && find(o.value("window").toString())==m_hostTarget) {
        workspace()->activateWindow(m_hostTarget);
        ok=keyboardReady();
        m_hostKeyboardUsed=ok;
    } else if(op=="key" && keyboardReady() && o.value("code").isDouble() && o.value("pressed").isBool()) {
        double code=o.value("code").toDouble(); bool down=o.value("pressed").toBool();
        if(code>=1 && code<=247 && code==std::floor(code) && m_hostKeys.contains(uint32_t(code))!=down) {
            if(down) m_hostKeys.append(uint32_t(code)); else m_hostKeys.removeOne(uint32_t(code));
            m_hostKeyboardUsed=true;
            input()->keyboard()->processKey(uint32_t(code),down?KeyboardKeyState::Pressed:KeyboardKeyState::Released,hostTime(),&m_hostDevice);
            ok=keyboardReady();
        }
    } else if(op=="move" && o.value("x").isDouble() && o.value("y").isDouble()) {
        const QPointF p(o.value("x").toDouble(),o.value("y").toDouble());
        if(std::isfinite(p.x()) && std::isfinite(p.y()) && at(p)==m_hostTarget && m_hostTarget->clientGeometry().contains(p)) {
            input()->pointer()->processMotionAbsolute(p,hostTime(),&m_hostDevice);
            input()->pointer()->processFrame(&m_hostDevice);
            ok=hostValid() && QLineF(input()->pointer()->pos(),p).length()<1;
        }
    } else if((op=="button" || op=="scroll") && at(input()->pointer()->pos())==m_hostTarget
              && waylandServer()->seat()->focusedPointerSurface()
              && waylandServer()->seat()->focusedPointerSurface()->client()==m_hostClient) {
        if(op=="button" && o.value("code").isDouble() && o.value("pressed").isBool()) {
            double code=o.value("code").toDouble(); bool down=o.value("pressed").toBool();
            if(code>=272 && code<=274 && code==std::floor(code) && m_hostButtons.contains(uint32_t(code))!=down) {
                if(down) m_hostButtons.insert(uint32_t(code)); else m_hostButtons.remove(uint32_t(code));
                input()->pointer()->processButton(uint32_t(code),down?PointerButtonState::Pressed:PointerButtonState::Released,hostTime(),&m_hostDevice);
                input()->pointer()->processFrame(&m_hostDevice); ok=true;
            }
        } else if(op=="scroll" && o.value("delta").isDouble() && (o.value("axis")=="vertical" || o.value("axis")=="horizontal")) {
            double delta=o.value("delta").toDouble();
            if(std::isfinite(delta) && std::abs(delta)<1000000) {
                input()->pointer()->processAxis(o.value("axis")=="vertical"?PointerAxis::Vertical:PointerAxis::Horizontal,
                    delta,0,PointerAxisSource::Continuous,false,hostTime(),&m_hostDevice);
                input()->pointer()->processFrame(&m_hostDevice); ok=true;
            }
        }
    }
    if(!ok && socket==m_hostOwner) { m_hostStopReason=error.isEmpty()?"rejected_action":error; releaseHost(); }
    if(socket==m_hostOwner) m_hostWatchdog.start();
    QJsonObject reply{{"ok",ok},{"lane","host"},{"lease",m_hostLease},{"generation",qint64(m_hostGeneration)},
        {"session",m_session},{"keyboard_ready",keyboardReady()}};
    if(!ok) reply.insert("error",error.isEmpty()?"host_operation_rejected":error);
    return reply;
}

void Artist::drawCursor()
{
    if (m_session.isEmpty() || !m_positionKnown) {
        if (m_cursor) m_cursor->setVisible(false);
        return;
    }
    if (!kwinApp()->scene()) {
        return;
    }
    if (!m_cursor) {
        // A separate scene item preserves the real human cursor and its hardware plane.
        m_cursor = new ImageItem(kwinApp()->scene()->overlayItem());
        m_cursor->setZ(10000);
    }
    m_cursor->setVisible(!waylandServer()->isScreenLocked());
    if (waylandServer()->isScreenLocked()) {
        return;
    }

    const qint64 now = m_clock.elapsed();
    constexpr qreal trailDuration = 180;
    while (!m_trail.isEmpty() && now - m_trail.front().first > trailDuration) {
        m_trail.removeFirst();
    }
    const bool moving = now - m_lastMove < 160;
    m_animation.setInterval(moving || now - m_lastClick < 500 ? 16 : 33);
    const qreal breathe = .5 + .5 * std::sin(now / 1700.0 * 2 * std::numbers::pi);
    const qreal energy = moving ? 1.0 : (m_target ? .65 : .4);

    QRectF bounds(m_position - QPointF(48, 48), QSizeF(112, 128));
    for (const auto &[time, point] : std::as_const(m_trail)) {
        bounds |= QRectF(point - QPointF(18, 18), QSizeF(36, 36));
    }
    // Bound allocation for large warps. A warp is not a visual sweep across the desktop.
    if (bounds.width() > 400 || bounds.height() > 400) {
        m_trail.clear();
        bounds = QRectF(m_position - QPointF(48, 48), QSizeF(112, 128));
    }
    bounds = bounds.toAlignedRect();
    const qreal scale = kwinApp()->devicePixelRatio();
    QImage image(QSize(qCeil(bounds.width() * scale), qCeil(bounds.height() * scale)), QImage::Format_ARGB32_Premultiplied);
    image.setDevicePixelRatio(scale);
    image.fill(Qt::transparent);
    QPainter p(&image);
    p.setRenderHint(QPainter::Antialiasing);
    p.translate(-bounds.topLeft());

    // A short luminous wake, fading by sample age. It never leads actual input.
    for (qsizetype i = 1; i < m_trail.size(); ++i) {
        const qreal age = (now - m_trail[i - 1].first) / trailDuration;
        const qreal alpha = std::clamp(1.0 - age, 0.0, 1.0);
        QLinearGradient gradient(m_trail[i - 1].second, m_trail[i].second);
        gradient.setColorAt(0, QColor(151, 113, 255, int(95 * alpha)));
        gradient.setColorAt(1, QColor(88, 221, 255, int(125 * alpha)));
        p.setPen(QPen(QBrush(gradient), 8 * alpha + 1, Qt::SolidLine, Qt::RoundCap));
        p.drawLine(m_trail[i - 1].second, m_trail[i].second);
        p.setPen(QPen(QColor(189, 246, 255, int(125 * alpha)), 1.4 * alpha, Qt::SolidLine, Qt::RoundCap));
        p.drawLine(m_trail[i - 1].second, m_trail[i].second);
    }

    p.translate(m_position);
    QRadialGradient halo(QPointF(8, 12), 34 + 3 * breathe);
    halo.setColorAt(0, QColor(112, 186, 255, int(75 * energy)));
    halo.setColorAt(.45, QColor(123, 104, 255, int((30 + 15 * breathe) * energy)));
    halo.setColorAt(1, Qt::transparent);
    p.setPen(Qt::NoPen);
    p.setBrush(halo);
    p.drawEllipse(QPointF(8, 12), 38, 38);

    const qreal clickAge = (now - m_lastClick) / 450.0;
    if (clickAge >= 0 && clickAge < 1) {
        const qreal radius = 5 + 30 * (1 - std::pow(1 - clickAge, 3));
        p.setBrush(Qt::NoBrush);
        p.setPen(QPen(QColor(108, 226, 255, int(180 * std::pow(1 - clickAge, 2))), 1.5));
        p.drawEllipse(QPointF(0, 0), radius, radius);
    }

    qreal targetLean = 0;
    if (m_trail.size() > 1) {
        targetLean = std::clamp((m_position.x() - m_trail.front().second.x()) * .12, -7.0, 7.0);
    }
    m_lean += (targetLean - m_lean) * .2;
    p.save();
    p.rotate(m_lean); // Rotate around the exact input hotspot, not the arrow center.
    QPainterPath arrow;
    arrow.moveTo(0, 0);
    arrow.cubicTo(-.5, -.7, -1.3, -.3, -1.2, .8);
    arrow.lineTo(.1, 26.8);
    arrow.cubicTo(.2, 28, 1.1, 28.4, 2, 27.6);
    arrow.lineTo(8.2, 21.7);
    arrow.lineTo(13.8, 33.1);
    arrow.quadTo(14.4, 34.3, 15.5, 33.7);
    arrow.lineTo(19.1, 31.8);
    arrow.quadTo(20.1, 31.3, 19.5, 30.1);
    arrow.lineTo(13.9, 19.3);
    arrow.lineTo(23, 18.8);
    arrow.quadTo(24.7, 18.7, 23.4, 17.5);
    arrow.closeSubpath();

    // Layered outlines soften the glow without requiring a blur shader.
    p.setBrush(Qt::NoBrush);
    for (int width = 10; width >= 3; width -= 2) {
        p.setPen(QPen(QColor(103, 179, 255, int((12 - width) * energy * 3)), width, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
        p.drawPath(arrow);
    }
    QLinearGradient silver(-2, 0, 22, 32);
    silver.setColorAt(0, QColor(255, 255, 255));
    silver.setColorAt(.38, QColor(232, 239, 250));
    silver.setColorAt(1, QColor(157, 172, 207));
    p.setBrush(silver);
    p.setPen(QPen(QColor(43, 53, 80, 225), 1.2, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
    p.drawPath(arrow);
    p.setPen(QPen(QColor(255, 255, 255, 220), .8, Qt::SolidLine, Qt::RoundCap));
    p.drawLine(QPointF(1.1, 3), QPointF(2.1, 22));
    p.restore();

    // A small orbiting spark identifies the agent even when it is standing by.
    const qreal phase = now / 2300.0 * 2 * std::numbers::pi;
    const QPointF spark(8 + 19 * std::cos(phase), 13 + 19 * std::sin(phase));
    QRadialGradient sparkGlow(spark, 6);
    sparkGlow.setColorAt(0, QColor(156, 235, 255, 200));
    sparkGlow.setColorAt(.3, QColor(113, 188, 255, 120));
    sparkGlow.setColorAt(1, Qt::transparent);
    p.setPen(Qt::NoPen);
    p.setBrush(sparkGlow);
    p.drawEllipse(spark, 6, 6);
    p.setBrush(QColor(219, 250, 255));
    p.drawEllipse(spark, 1.3, 1.3);
    p.end();
    m_cursor->setImage(image);
    m_cursor->setSize(bounds.size());
    m_cursor->setPosition(bounds.topLeft());
}

class Factory : public PluginFactory {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID PluginFactory_iid FILE "metadata.json")
    Q_INTERFACES(KWin::PluginFactory)
public:
    std::unique_ptr<Plugin> create() const override {
        auto plugin=std::make_unique<Artist>();
        if(!plugin->start()) return nullptr;
        return plugin;
    }
};
}
#include "main.moc"
