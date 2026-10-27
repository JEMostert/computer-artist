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
#include <config-kwin.h>
#include <core/output.h>
#include "clipboard.h"
#include "keyboard.h"
#include "capture.h"
#include <wayland_server.h>
#include <wayland/clientconnection.h>
#include <wayland/display.h>
#include <wayland/surface.h>
#include <wayland/xdgshell.h>
#include <wayland/seat.h>
#include <wayland/pointer.h>
#include <scene/imageitem.h>
#include <scene/workspacescene.h>
#include <wayland-server-core.h>
#include <wayland-server-protocol.h>
#include <QCoreApplication>
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
#include <QRandomGenerator>
#include <QUuid>
#include <QSet>
#include <QTemporaryFile>
#include <QThreadPool>
#include <unistd.h>
#include <algorithm>
#include <cmath>
#include <numbers>
#include <functional>
#include <utility>

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
    bool agentKeyboardReady() const;
    bool acquireHost(Window *, QLocalSocket *);
    void releaseHost(bool revoke = false, uint32_t transferredButton = 0, uint32_t transferredKey = 0);
    QJsonObject requestHost(QLocalSocket *, const QJsonObject &);
    std::chrono::microseconds hostTime() const { return std::chrono::microseconds(m_clock.nsecsElapsed()/1000); }
    HostDevice m_hostDevice;
    ArtistKeyboard m_agentKeyboard;
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
    QJsonObject finish(QLocalSocket *, const QString &lane, QJsonObject reply, bool ok);
    QJsonArray windows() const;
    Window *find(const QString &id) const;
    bool visible(Window *) const;
    Window *at(const QPointF &) const;
    bool popupFor(Window *, Window *) const;
    bool pointerTarget(Window *) const;
    bool pointerReady() const;
    bool owns(SurfaceInterface *s) const { return m_client && s && s->client() == m_client; }
    QList<uint32_t> pointers(ClientConnection *client = nullptr) const;
    QStringList acquisitionReasons(Window *, bool host) const;
    void send(const std::function<void(wl_resource *)> &);
    void frame();
    bool acquire(Window *, QLocalSocket *);
    bool move(const QPointF &);
    bool button(uint32_t, bool);
    void cancel();
    void cancelPointer();
    void release(bool revoke = false, const QString &reason = QStringLiteral("released"));
    QString invalidReason() const;
    void drawCursor();
    QPointF shownPosition(qint64 now) const;
    void observe(Window *);
    bool valid() const;
    QLocalServer m_server;
    QThreadPool m_encoder; // PNG encoding and writing, off the compositor thread.
    QPointer<ClientConnection> m_client;
    QPointer<Window> m_target;
    QPointer<SurfaceInterface> m_surface;
    struct AgentPopup {
        QPointer<Window> window;
        QPointer<XdgPopupInterface> role;
        QPointer<ClientConnection> client;
    };
    QList<AgentPopup> m_agentPopups;
    QList<AgentPopup> m_pendingPopups;
    QTimer m_popupDismissTimer;
    void dismissPopups();
    QPointer<QLocalSocket> m_owner;
    QList<uint32_t> m_resources;
    QSet<uint32_t> m_buttons;
    QString m_lease;
    QString m_stopReason;
    QString m_session;
    quint64 m_generation = 0;
    QMetaObject::Connection m_clientClosed;
    QTimer m_watchdog;
    QTimer m_animation;
    QPointer<ImageItem> m_cursor;
    QPointF m_position;
    QElapsedTimer m_clock;
    // Ink cursor state; computer_artist/ink_cursor.py renders the same design elsewhere.
    struct InkSample { qint64 time; QPointF point; bool pressed; };
    struct InkDrop { qint64 born; QPointF origin, velocity; qreal radius; };
    QList<InkSample> m_trail;
    QList<InkDrop> m_drops;
    QPointF m_clickPosition;
    int m_clickLobes = 6;
    qreal m_clickPhase = 0;
    qint64 m_lastMove = -1000, m_lastClick = -1000;
    qreal m_lean = 0;
    bool m_positionKnown = false;
    // The drawn pen glides from m_glideFrom to the input position; see ink_cursor.glide.
    QPointF m_glideFrom;
    qint64 m_glideStart = 0, m_glideMs = 0;
};

Artist::Artist() {
    m_clock.start();
    m_popupDismissTimer.setSingleShot(true);
    connect(&m_popupDismissTimer, &QTimer::timeout, this, &Artist::dismissPopups);
    input()->addInputDevice(&m_hostDevice);
    input()->installInputEventSpy(this);
    m_hostWatchdog.setSingleShot(true);
    m_hostWatchdog.setInterval(5000);
    connect(&m_hostWatchdog, &QTimer::timeout, this, [this] { m_hostStopReason="watchdog"; releaseHost(true); });
    m_watchdog.setSingleShot(true);
    m_watchdog.setInterval(5000);
    connect(&m_watchdog, &QTimer::timeout, this, [this] { release(true, QStringLiteral("watchdog")); });
    connect(&m_animation, &QTimer::timeout, this, [this] {
        if (m_client && !valid()) release(true, invalidReason());
        if (m_hostOwner && !hostValid()) { m_hostStopReason="target_unavailable"; releaseHost(true); }
        if (m_positionKnown) drawCursor();
    });
    // This signal runs before PointerInputRedirection::update() sends human enter.
    connect(input(), &InputRedirection::globalPointerChanged, this, [this](const QPointF &p) {
        auto w = at(p);
        if (w && owns(w->surface())) release(true, QStringLiteral("human_pointer_entered"));
    });
    connect(waylandServer()->seat(), &SeatInterface::focusedKeyboardSurfaceAboutToChange,
            this, [this](SurfaceInterface *s) {
        if (owns(s)) release(true, QStringLiteral("human_keyboard_focus"));
        if (m_hostOwner && m_hostKeyboardUsed && (!m_hostTarget || s!=m_hostTarget->surface())) {
            m_hostStopReason="keyboard_focus_changed"; releaseHost(true);
        }
    });
    // Catch stationary-pointer focus changes from stacking/geometry, too. A human
    // enter may already have been sent here; release must preserve that enter.
    connect(waylandServer()->seat()->pointer(), &PointerInterface::focusedSurfaceChanged,
            this, [this] { if (owns(waylandServer()->seat()->focusedPointerSurface())) release(true, QStringLiteral("human_pointer_entered")); });
    // A client-specific agent enter does not establish a host-seat grab. Keep
    // popups created during our lease out of KWin's global popup input filter.
    // This connection runs at creation, before the client's grab request.
    connect(waylandServer(), &WaylandServer::windowCreated, this, [this](Window *w) {
        if (!m_client || !valid() || !w->inherits("KWin::XdgPopupWindow") || !owns(w->surface())) return;
        struct Lookup { SurfaceInterface *surface; XdgPopupInterface *popup = nullptr; } lookup{w->surface()};
        wl_client_for_each_resource(m_client->client(), [](wl_resource *r, void *data) {
            auto lookup=static_cast<Lookup *>(data);
            if (strcmp(wl_resource_get_class(r), "xdg_popup")==0) {
                auto popup=XdgPopupInterface::get(r);
                if (popup && popup->surface()==lookup->surface) lookup->popup=popup;
            }
            return WL_ITERATOR_CONTINUE;
        }, &lookup);
        if (!lookup.popup) return;
        auto parent=waylandServer()->findWindow(lookup.popup->parentSurface());
        if (parent!=m_target && !popupFor(parent, m_target)) return;
        if (QObject::disconnect(lookup.popup, SIGNAL(grabRequested(KWin::SeatInterface*,quint32)), w, nullptr))
            m_agentPopups.append({w, lookup.popup, m_client});
    });
    for (auto w : workspace()->stackingOrder()) observe(w);
    connect(workspace(), &Workspace::windowAdded, this, [this](Window *w) {
        observe(w);
        if (w->surface() && owns(w->surface()) && !popupFor(w, m_target)) release(true, QStringLiteral("new_application_window"));
        if (m_hostClient && w->surface() && w->surface()->client()==m_hostClient) {
            m_hostStopReason="new_application_window"; releaseHost(true);
        }
    });
}
Artist::~Artist() {
    m_encoder.waitForDone(); // Plugin code must not run after unload.
    m_animation.stop();
    release(true, QStringLiteral("plugin_unloaded"));
    m_popupDismissTimer.stop();
    dismissPopups();
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
    if (!QFile::setPermissions(dir, QFile::ReadOwner | QFile::WriteOwner | QFile::ExeOwner)) return false;
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
                if (m_owner == socket) release(false, QStringLiteral("disconnect"));
                if (m_hostOwner == socket) { m_hostStopReason="disconnect"; releaseHost(); }
                socket->deleteLater();
            });
            connect(socket, &QLocalSocket::readyRead, this, [this,socket] {
                while (socket->canReadLine() && !socket->property("revoked").toBool()) {
                    if(socket->property("reply_pending").toBool()) { socket->abort(); return; }
                    // readLine returns at most maxSize-1 bytes; a longer line must
                    // abort rather than be answered as several invalid requests.
                    auto line = socket->readLine(65538);
                    if (line.size() > 65536 || !line.endsWith('\n')) { socket->abort(); return; }
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
// Only explicitly parented native popup descendants belong to a target. A
// second document, dialog, tooltip or unrelated client is not an input target.
bool Artist::popupFor(Window *w, Window *target) const {
    if (!w || !target || !w->inherits("KWin::XdgPopupWindow")
        || !w->surface() || !target->surface()
        || w->surface()->client()!=target->surface()->client()) return false;
    QSet<Window *> seen;
    for (auto parent=w->transientFor(); parent && !seen.contains(parent); parent=parent->transientFor()) {
        if (parent==target) return true;
        seen.insert(parent);
    }
    return false;
}
bool Artist::pointerTarget(Window *w) const {
    return w && (w==m_target || popupFor(w, m_target)) && visible(w);
}
bool Artist::pointerReady() const {
    auto w=at(m_position);
    if (!pointerTarget(w) || !w->surface() || !m_surface) return false;
    const auto [surface, local]=w->surface()->mapToInputSurface(w->inputTransformation().map(m_position));
    return surface==m_surface && !surface->lockedPointer() && !surface->confinedPointer();
}
QList<uint32_t> Artist::pointers(ClientConnection *client) const {
    QList<uint32_t> result;
    if (!client) client = m_client;
    if (!client || client->tearingDown()) return result;
    wl_client_for_each_resource(client->client(), [](wl_resource *r, void *data) {
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
    if (m_agentKeyboard.initialized() && !m_agentKeyboard.ready()) return false;
    for (auto w : workspace()->stackingOrder()) if (owns(w->surface()) && (w->hasPopupGrab() || (w->isSpecialWindow() && !(w==m_target && w->inherits("KWin::XdgPopupWindow")) && !popupFor(w, m_target)))) return false;
    return true;
}
QString Artist::invalidReason() const {
    if (!m_client) return QStringLiteral("released");
    if (!visible(m_target)) return QStringLiteral("target_unavailable");
    if (waylandServer()->isScreenLocked()) return QStringLiteral("screen_locked");
    auto seat = waylandServer()->seat();
    if (owns(seat->focusedPointerSurface())) return QStringLiteral("human_pointer_entered");
    if (owns(seat->focusedKeyboardSurface())) return QStringLiteral("human_keyboard_focus");
    if (seat->isDrag() || seat->isTouchSequence()) return QStringLiteral("seat_gesture_active");
    if (m_agentKeyboard.initialized() && !m_agentKeyboard.ready()) return QStringLiteral("keyboard_resources_changed");
    return QStringLiteral("application_popup_or_special_window");
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
        if (m_client && owns(w->surface()) && !popupFor(w, m_target)) { ++m_generation; release(true, QStringLiteral("target_changed")); }
        if (m_hostTarget == w) { ++m_hostGeneration; m_hostStopReason="target_changed"; releaseHost(true); }
    };
    connect(w, &Window::closed, this, changed);
    connect(w, &Window::frameGeometryChanged, this, changed);
    connect(w, &Window::minimizedChanged, this, changed);
    connect(w, &Window::hiddenChanged, this, changed);
}
QStringList Artist::acquisitionReasons(Window *w, bool host) const {
    QStringList reasons;
    if (!visible(w)) reasons.append("target_not_visible");
    if (!w || !w->surface()) { reasons.append("surface_unavailable"); return reasons; }
    auto client = w->surface()->client();
    auto seat = waylandServer()->seat();
    auto sameClient = [client](SurfaceInterface *surface) { return surface && surface->client() == client; };
    if (client == waylandServer()->xWaylandConnection()) reasons.append("xwayland_unsupported");
    if ((!w->inherits("KWin::XdgToplevelWindow") && !w->inherits("KWin::XdgPopupWindow"))
        || (w->isSpecialWindow() && !w->inherits("KWin::XdgPopupWindow"))) reasons.append("window_type_unsupported");
    if (waylandServer()->isScreenLocked()) reasons.append("screen_locked");
    if (seat->isDrag() || seat->isTouchSequence()) reasons.append("seat_gesture_active");
    if (!input()->keyboard()->pressedKeys().isEmpty()) reasons.append("keyboard_keys_held");
    Qt::MouseButtons syntheticHostButtons = Qt::NoButton;
    if (!host) {
        if (m_hostButtons.contains(272)) syntheticHostButtons |= Qt::LeftButton;
        if (m_hostButtons.contains(273)) syntheticHostButtons |= Qt::RightButton;
        if (m_hostButtons.contains(274)) syntheticHostButtons |= Qt::MiddleButton;
    }
    if ((input()->pointer()->buttons() & ~syntheticHostButtons) != Qt::NoButton) reasons.append("pointer_buttons_held");
    if (host ? bool(m_hostOwner) : bool(m_client)) reasons.append("lane_busy");
    if (host ? owns(w->surface()) : m_hostClient == client) reasons.append("other_lane_owns_application");
    for (auto sibling : workspace()->stackingOrder()) {
        if (sameClient(sibling->surface()) && (sibling->hasPopupGrab() || (sibling->isSpecialWindow()
                && !( !host && (sibling==w || popupFor(sibling, w) || popupFor(w, sibling)))))) {
            reasons.append("application_popup_or_special_window"); break;
        }
    }
    if (host) {
        if (input()->pointer()->isConstrained()) reasons.append("pointer_constrained");
    } else {
        if (sameClient(seat->focusedPointerSurface())) reasons.append("human_pointer_in_application");
        if (sameClient(seat->focusedKeyboardSurface())) reasons.append("human_keyboard_in_application");
        if (pointers(client).isEmpty()) reasons.append("pointer_resources_unavailable");
    }
    return reasons;
}
bool Artist::acquire(Window *w, QLocalSocket *socket) {
    if (!acquisitionReasons(w, false).isEmpty()) return false;
    m_client = w->surface()->client();
    m_target = w;
    if (!valid()) { m_client.clear(); m_target.clear(); return false; }
    m_resources = pointers();
    if (m_resources.isEmpty()) { m_client.clear(); m_target.clear(); return false; }
    m_clientClosed = connect(m_client, &ClientConnection::aboutToBeDestroyed, this, [this] {
        // tearingDown() becomes true only AFTER this signal. Do not schedule
        // protocol events for a connection that is already being destroyed.
        m_agentPopups.clear();
        release(true, QStringLiteral("application_closed"));
    });
    m_owner = socket;
    m_lease = QUuid::createUuid().toString(QUuid::WithoutBraces);
    m_stopReason.clear();
    ++m_generation;
    m_watchdog.start();
    ensureSession();
    return true;
}
bool Artist::move(const QPointF &p) {
    if (!valid() || !std::isfinite(p.x()) || !std::isfinite(p.y()) || pointers() != m_resources) return false;
    auto w = at(p);
    if (!w || !pointerTarget(w) || !w->clientGeometry().contains(p)) return false;
    auto [surface, local] = w->surface()->mapToInputSurface(w->inputTransformation().map(p));
    if (!surface || surface->lockedPointer() || surface->confinedPointer()) return false;
    local = surface->toSurfaceLocal(local);
    if (m_surface != surface) {
        if (!m_buttons.isEmpty()) return false;
        cancelPointer();
        m_surface = surface;
        const auto serial = agentSerial();
        send([&](wl_resource *r) { wl_pointer_send_enter(r, serial, surface->resource(), wl_fixed_from_double(local.x()), wl_fixed_from_double(local.y())); });
    } else {
        send([&](wl_resource *r) { wl_pointer_send_motion(r, uint32_t(m_clock.elapsed()), wl_fixed_from_double(local.x()), wl_fixed_from_double(local.y())); });
    }
    frame();
    const qint64 now = m_clock.elapsed();
    const QPointF shown = m_positionKnown ? shownPosition(now) : p;
    m_glideMs = 0;
    if (m_positionKnown) {
        const QPointF step = p - m_position;
        const qreal distance = std::hypot(step.x(), step.y());
        const qreal travel = std::hypot(p.x() - shown.x(), p.y() - shown.y());
        auto *random = QRandomGenerator::global();
        if (m_buttons.isEmpty() && travel >= 24) {
            // A new action's pen travels there visibly; the client sends its next input on arrival.
            m_glideFrom = shown;
            m_glideStart = now;
            m_glideMs = qint64(std::min(220.0, 60 + travel * .3));
        } else if (distance > 520) {
            m_trail.clear(); // A warp is not a visible sweep across the desktop.
        } else if (distance > 9 && random->generateDouble() <= std::min(.85, distance / 40)) {
            // Fast strokes shed droplets sideways and slightly behind the nib.
            const QPointF direction = step / distance;
            const qreal side = random->bounded(2) ? 1 : -1, speed = 25 + random->generateDouble() * 45;
            m_drops.append({now, p - direction * 6,
                QPointF(-direction.y() * side - direction.x() * .4, direction.x() * side - direction.y() * .4) * speed,
                .9 + random->generateDouble() * 1.6});
        }
    }
    const bool firstPosition = !m_positionKnown;
    m_positionKnown = true;
    m_position = p;
    m_trail.append({now, m_glideMs ? shown : p, !m_buttons.isEmpty()});
    m_lastMove = now;
    // Input can arrive much faster than the display refresh. Coalesce visual
    // updates on the animation timer; dispatch above is never delayed.
    if (m_animation.interval() != 16) m_animation.setInterval(16);
    if (firstPosition) drawCursor();
    return true;
}
QPointF Artist::shownPosition(qint64 now) const {
    if (now - m_glideStart >= m_glideMs) return m_position;
    const qreal eased = 1 - std::pow(1 - qreal(now - m_glideStart) / m_glideMs, 3);
    return m_glideFrom + (m_position - m_glideFrom) * eased;
}
bool Artist::button(uint32_t code, bool down) {
    if (!valid() || !m_surface || pointers()!=m_resources || !pointerReady()
        || (code < 272 || code > 274) || m_buttons.contains(code)==down) return false;
    const qint64 now = m_clock.elapsed();
    m_glideMs = 0;
    if (down && m_buttons.isEmpty() && m_positionKnown) {
        // An ink splash: a wobbling ring, a blot and droplets flung outward.
        auto *random = QRandomGenerator::global();
        m_lastClick = now;
        m_clickPosition = m_position;
        m_clickLobes = 5 + random->bounded(3);
        m_clickPhase = random->generateDouble() * 6.3;
        for (int i = 0; i < 6; ++i) {
            const qreal angle = random->generateDouble() * 2 * std::numbers::pi, speed = 70 + random->generateDouble() * 90;
            m_drops.append({now, m_position, QPointF(std::cos(angle), std::sin(angle)) * speed, 1.4 + random->generateDouble() * 1.8});
        }
    }
    if (down) m_buttons.insert(code); else m_buttons.remove(code);
    if (m_positionKnown) m_trail.append({now, m_position, !m_buttons.isEmpty()});
    if (m_animation.interval() != 16) m_animation.setInterval(16);
    auto serial = agentSerial();
    send([&](wl_resource *r) { wl_pointer_send_button(r, serial, uint32_t(m_clock.elapsed()), code, down ? WL_POINTER_BUTTON_STATE_PRESSED : WL_POINTER_BUTTON_STATE_RELEASED); });
    frame();
    return true;
}
void Artist::cancel() {
    m_agentKeyboard.cancel(uint32_t(m_clock.elapsed()));
    cancelPointer();
}
bool Artist::agentKeyboardReady() const {
    return valid() && m_target && m_target->surface()
        && (m_agentKeyboard.initialized() ? m_agentKeyboard.ready()
            : !ArtistKeyboard::resources(m_client).isEmpty());
}
void Artist::cancelPointer() {
    for (auto code : std::as_const(m_buttons)) {
        auto serial = agentSerial();
        send([&](wl_resource *r) { wl_pointer_send_button(r, serial, uint32_t(m_clock.elapsed()), code, WL_POINTER_BUTTON_STATE_RELEASED); });
    }
    m_buttons.clear();
    // Never erase a new human enter that stock KWin has already delivered.
    if (m_surface && !owns(waylandServer()->seat()->focusedPointerSurface())) {
        auto serial = agentSerial();
        send([&](wl_resource *r) { wl_pointer_send_leave(r, serial, m_surface->resource()); });
    }
    frame();
    m_surface.clear();
}
void Artist::release(bool revoke, const QString &reason) {
    // Record why before aborting: a revoked CLI only sees its connection drop and
    // asks session_status on a fresh connection for the reason.
    if (m_client) m_stopReason = reason;
    auto owner = m_owner;
    m_owner.clear();
    m_watchdog.stop();
    // Window::closed can run inside an XDG role destructor. KWin also dismisses
    // child popups from that signal. Finish revocation now, but let that entire
    // teardown/parent-close dispatch unwind before touching popup roles.
    m_pendingPopups.append(std::exchange(m_agentPopups, {}));
    if (!m_pendingPopups.isEmpty()) m_popupDismissTimer.start(0);
    cancelPointer();
    m_agentKeyboard.end(uint32_t(m_clock.elapsed()));
    disconnect(m_clientClosed);
    m_client.clear(); m_target.clear(); m_resources.clear(); m_lease.clear();
    if (revoke && owner) { owner->setProperty("revoked",true); owner->abort(); }
}
void Artist::dismissPopups() {
    const auto popups = std::exchange(m_pendingPopups, {});
    // Nested popups must be dismissed before their parents. QPointer<Window>
    // alone is insufficient: effects can retain a deleted window beyond its
    // XDG role lifetime, and popupDone() is not safe to call twice.
    for (auto it = popups.crbegin(); it != popups.crend(); ++it) {
        if (it->window && !it->window->isDeleted() && it->role
            && it->client && !it->client->tearingDown()) it->window->popupDone();
    }
}
QJsonArray Artist::windows() const {
    QJsonArray result;
    for (auto w : workspace()->stackingOrder()) {
        if (w->isDeleted() || !w->surface()) continue;
        auto rect = w->clientGeometry();
        const bool native = w->surface()->client()!=waylandServer()->xWaylandConnection();
        const auto agentReasons = acquisitionReasons(w, false);
        const auto hostReasons = acquisitionReasons(w, true);
        result.append(QJsonObject{{"id",w->internalId().toString(QUuid::WithoutBraces)}, {"title",w->caption()},
            {"pid",int(w->surface()->client()->processId())},{"native",native},{"backend",native?"wayland":"xwayland"},
            {"parent",w->transientFor()?w->transientFor()->internalId().toString(QUuid::WithoutBraces):QString()},
            {"role",w->inherits("KWin::XdgPopupWindow")?"popup":w->isDialog()?"dialog":"toplevel"},
            {"popup_grab",w->hasPopupGrab()},{"modal",w->isModal()},
            {"observable",visible(w) && (!w->isSpecialWindow() || w->inherits("KWin::XdgPopupWindow"))},
            {"agent_keyboard",native && !ArtistKeyboard::resources(w->surface()->client()).isEmpty()},
            {"visible",visible(w)},{"agent",owns(w->surface())},{"host",m_hostClient && w->surface()->client()==m_hostClient},{"human_active",workspace()->activeWindow()==w},
            {"acquirable",agentReasons.isEmpty()},
            {"agent_restrictions",QJsonArray::fromStringList(agentReasons)},
            {"host_acquirable",hostReasons.isEmpty()},
            {"host_restrictions",QJsonArray::fromStringList(hostReasons)},
            {"app_id",w->desktopFileName()},{"resource_class",w->resourceClass()},
            {"scale",w->targetScale()},{"output",w->output()?w->output()->name():QString()},
            {"maximized",w->maximizeMode()==MaximizeFull},{"fullscreen",w->isFullScreen()},
            {"x",rect.x()},{"y",rect.y()},{"width",rect.width()},{"height",rect.height()}});
    }
    return result;
}
QJsonObject Artist::request(QLocalSocket *socket, const QJsonObject &o) {
    const auto op = o.value("op").toString();
    const auto lane = o.value("lane").toString("agent");
    if (lane!="agent" && lane!="host") return {{"ok",false},{"error","invalid_lane"}};
    const bool observation = op=="windows" || op=="capabilities" || op=="ping" || op=="capture" || op=="keymap";
    if (lane=="host" && !observation && op!="session_status" && op!="session_close") return requestHost(socket,o);
    QJsonObject reply;
    bool ok=false;
    if (!observation && op!="takeover" && op!="session_close" && op!="session_status" && lane=="agent" && m_owner && m_owner!=socket) reply.insert("error","controller_busy");
    else if (op=="capabilities") {
        reply = {{"environment",qEnvironmentVariable("CA_ENVIRONMENT")},{"protocol",3},{"backend","stock_kwin_plugin"},
            {"build",QJsonObject{{"source_sha256",QStringLiteral(CA_PLUGIN_SOURCE_SHA256)},{"kwin_headers",QString(KWIN_VERSION_STRING)},{"kwin_running",QCoreApplication::applicationVersion()}}},{"ownership","wayland_connection_pointer_keyboard"},
            {"automatic_sessions",true},{"lane",lane},{"host_pointer",true},{"host_focus",true},{"host_keyboard",true},{"host_xwayland",false},{"native_handoff",true},{"xwayland_handoff",false},{"keyboard",true},{"input_methods",false},
            {"clipboard",false},{"data_drag_and_drop",false},{"popups",false},{"popup_observation",true},{"ungrabbed_popup_pointer",true},{"capture_transients",true},{"human_pointer_entry_takeover",true},
            {"operations",QJsonArray{"session_close","session_status","windows","capabilities","acquire","release","move","button","scroll","keyboard_begin","key","keymap","cancel","takeover","ping","capture"}}};
        if (lane=="host") {
            reply.insert("ownership","host_wayland_connection");
            reply.insert("keyboard",true);
            const bool clipboard=m_clipboard && m_clipboard->ready();
            reply.insert("clipboard",clipboard);
            reply.insert("clipboard_max_bytes",Clipboard::MaxBytes);
            auto operations=QJsonArray{"session_close","session_status","windows","capabilities","acquire","release","move","button","scroll","focus","key","keymap","cancel","takeover","ping","capture"};
            if(clipboard) { operations.append("clipboard_get"); operations.append("clipboard_set"); }
            reply.insert("operations",operations);
        }
        ok=true;
    } else if (op=="windows") { reply.insert("windows",windows()); ok=true; }
    else if (op=="keymap") {
        const auto map=ArtistKeyboard::characters();
        for (auto it=map.begin(); it!=map.end(); ++it) reply.insert(it.key(),it.value());
        ok=map.contains("characters");
    }
    else if (op=="session_status") ok=true;
    else if (op=="session_close") {
        // Session lifetime is explicit and independent of short-lived CLI sockets.
        release(m_owner != socket, QStringLiteral("session_closed"));
        m_hostStopReason="session_closed";
        releaseHost(m_hostOwner != socket);
        m_session.clear();
        m_animation.stop();
        m_positionKnown = false;
        m_glideMs = 0;
        m_trail.clear();
        m_drops.clear();
        m_lastMove = m_lastClick = -1000;
        m_lean = 0;
        if (m_cursor) m_cursor->setVisible(false);
        ok=true;
    }
    else if (op=="ping") ok=true;
    else if (op=="acquire") {
        auto target=find(o.value("window").toString());
        ok=acquire(target,socket);
        if(!ok) {
            reply.insert("error","unavailable_or_human_focus_or_busy");
            reply.insert("restrictions",QJsonArray::fromStringList(acquisitionReasons(target,false)));
        }
    }
    else if (op=="takeover") { release(true, QStringLiteral("explicit_stop")); ok=true; }
    else if (op=="release") { if (!m_client || (socket==m_owner && o.value("lease").toString()==m_lease)) { release(); ok=true; } }
    else if (op=="cancel") { if(socket==m_owner) { cancel(); ok=true; } }
    else if (op=="capture") {
        auto w=find(o.value("window").toString());
        const auto path=o.value("path").toString();
        if (!waylandServer()->isScreenLocked() && w && (!w->isSpecialWindow() || w->inherits("KWin::XdgPopupWindow")) && w->surface() && visible(w)
            && QFileInfo(path).isAbsolute() && !QFileInfo::exists(path) && !QFileInfo(path).isSymLink()) {
            auto image = captureClient(w);
            if (!image.isNull()) {
                // Only rendering and readback need the compositor thread. Encoding a
                // 4K window took ~190 ms there and froze the whole desktop.
                socket->setProperty("reply_pending",true);
                QPointer<QLocalSocket> receiver=socket;
                m_encoder.start([this,receiver,image,path,lane] {
                    // Publish a complete PNG without replacing any destination that
                    // appeared during rendering. The temporary file is on the same
                    // filesystem; link() atomically fails when the name exists.
                    QTemporaryFile file(QFileInfo(path).absolutePath() + "/.ca-capture-XXXXXX");
                    const bool saved=file.open() && image.save(&file,"PNG") && file.flush()
                        && ::link(QFile::encodeName(file.fileName()).constData(), QFile::encodeName(path).constData()) == 0;
                    QMetaObject::invokeMethod(this, [this,receiver,path,lane,saved] {
                        if(!receiver || receiver->state()!=QLocalSocket::ConnectedState) return;
                        receiver->setProperty("reply_pending",false);
                        QJsonObject reply;
                        if(saved) { reply.insert("path",path); reply.insert("source","kwin_composited_client"); }
                        else reply.insert("error","capture_unavailable_or_path_exists");
                        receiver->write(QJsonDocument(finish(receiver,lane,reply,saved)).toJson(QJsonDocument::Compact)+'\n');
                    }, Qt::QueuedConnection);
                });
                return {{"pending",true}};
            }
        }
        if(!ok) reply.insert("error","capture_unavailable_or_path_exists");
    } else if (op=="move" || op=="button" || op=="scroll" || op=="key" || op=="keyboard_begin") {
        if (socket!=m_owner || !m_client || o.value("lease").toString()!=m_lease
            || o.value("generation").toInteger(-1)!=qint64(m_generation)) reply.insert("error","stale_lease_or_geometry");
        else if(op=="keyboard_begin" && agentKeyboardReady()) {
            ok=m_agentKeyboard.initialized() || m_agentKeyboard.begin(m_target->surface());
        }
        else if(op=="key" && agentKeyboardReady() && o.value("code").isDouble() && o.value("pressed").isBool()) {
            const auto code = o.value("code").toDouble();
            const bool pressed = o.value("pressed").toBool();
            if(code>=1 && code<=247 && code==std::floor(code)
                && (m_agentKeyboard.initialized() || (pressed && m_agentKeyboard.begin(m_target->surface()))))
                ok=m_agentKeyboard.key(uint32_t(code),pressed,uint32_t(m_clock.elapsed()));
        }
        else if(op=="move" && o.value("x").isDouble() && o.value("y").isDouble()) {
            ok=move({o.value("x").toDouble(),o.value("y").toDouble()});
            if(ok && m_glideMs) reply.insert("glide_ms",m_glideMs);
        }
        else if(op=="button" && o.value("code").isDouble() && o.value("pressed").isBool()) {
            const auto code=o.value("code").toDouble();
            if(code>=272 && code<=274 && code==std::floor(code)) ok=button(uint32_t(code),o.value("pressed").toBool());
        } else if(op=="scroll" && valid() && m_surface && pointers()==m_resources && pointerReady()
            && o.value("delta").isDouble() && (o.value("axis")=="vertical" || o.value("axis")=="horizontal")) {
            double delta=o.value("delta").toDouble();
            if(std::isfinite(delta) && std::abs(delta)<1000000) {
                const auto axis=o.value("axis")=="vertical" ? WL_POINTER_AXIS_VERTICAL_SCROLL : WL_POINTER_AXIS_HORIZONTAL_SCROLL;
                // A complete wheel frame: source, high-resolution or discrete
                // steps, then the continuous value. One notch is 15 / 120.
                const auto requested=o.value("v120").toDouble(0);
                const int v120=int(std::clamp(std::round(requested!=0 && std::isfinite(requested) ? requested : delta*8), -1.0e6, 1.0e6));
                send([&](wl_resource *r) {
                    const auto version=wl_resource_get_version(r);
                    if (version>=WL_POINTER_AXIS_SOURCE_SINCE_VERSION) wl_pointer_send_axis_source(r,WL_POINTER_AXIS_SOURCE_WHEEL);
                    if (version>=WL_POINTER_AXIS_VALUE120_SINCE_VERSION) { if (v120) wl_pointer_send_axis_value120(r,axis,v120); }
                    else if (version>=WL_POINTER_AXIS_DISCRETE_SINCE_VERSION && v120/120) wl_pointer_send_axis_discrete(r,axis,v120/120);
                    wl_pointer_send_axis(r,uint32_t(m_clock.elapsed()),axis,wl_fixed_from_double(delta));
                }); frame(); ok=true;
            }
        }
    }
    return finish(socket,lane,reply,ok);
}
QJsonObject Artist::finish(QLocalSocket *socket, const QString &lane, QJsonObject reply, bool ok) {
    if (!ok) { if(socket==m_owner) cancel(); if(!reply.contains("error")) reply.insert("error","rejected_or_unsupported"); }
    if(socket==m_owner) m_watchdog.start();
    if(socket==m_hostOwner) m_hostWatchdog.start();
    reply.insert("host_position",QJsonArray{input()->pointer()->pos().x(),input()->pointer()->pos().y()});
    reply.insert("session",m_session);
    reply.insert("cursor_visible",m_cursor && m_cursor->isVisible());
    reply.insert("lanes",QJsonObject{
        {"agent",QJsonObject{{"busy",bool(m_owner)},{"window",m_target?m_target->internalId().toString(QUuid::WithoutBraces):QString()}, {"buttons",int(m_buttons.size())},{"keys",m_agentKeyboard.heldCount()},{"keyboard",true},{"keyboard_ready",agentKeyboardReady()},{"stop_reason",m_stopReason}}},
        {"host",QJsonObject{{"busy",bool(m_hostOwner)},{"window",m_hostTarget?m_hostTarget->internalId().toString(QUuid::WithoutBraces):QString()}, {"buttons",int(m_hostButtons.size())},{"keys",int(m_hostKeys.size())},{"stop_reason",m_hostStopReason}}}});
    reply.insert("ok",ok); reply.insert("lease",lane=="host"?m_hostLease:m_lease);
    reply.insert("generation",qint64(lane=="host"?m_hostGeneration:m_generation)); reply.insert("keyboard_ready",lane=="host" ? keyboardReady() : agentKeyboardReady());
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
    if (!acquisitionReasons(w, true).isEmpty()) return false;
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
            socket->setProperty("reply_pending",true);
            QPointer<QLocalSocket> receiver=socket;
            auto done=[receiver](QJsonObject result) {
                if(!receiver || receiver->state()!=QLocalSocket::ConnectedState) return;
                receiver->setProperty("reply_pending",false);
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
        } else error=!m_hostTarget->clientGeometry().contains(p)?"point_outside_target_content":"target_occluded_at_point";
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
                    delta,0,PointerAxisSource::Continuous,false,
#ifdef CA_KWIN_68
                    false,
#endif
                    hostTime(),&m_hostDevice);
                input()->pointer()->processFrame(&m_hostDevice); ok=true;
            }
        }
    }
    if(!ok && socket==m_hostOwner) { m_hostStopReason=error.isEmpty()?"rejected_action":error; releaseHost(); }
    if(socket==m_hostOwner) m_hostWatchdog.start();
    QJsonObject reply{{"ok",ok},{"lane","host"},{"lease",m_hostLease},{"generation",qint64(m_hostGeneration)},
        {"session",m_session},{"keyboard_ready",keyboardReady()}};
    if(!ok) {
        reply.insert("error",error.isEmpty()?"host_operation_rejected":error);
        if(op=="acquire") reply.insert("restrictions",QJsonArray::fromStringList(
            acquisitionReasons(find(o.value("window").toString()),true)));
    }
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

    // The ink pen: palette, geometry and timings match computer_artist/ink_cursor.py.
    const QColor ink(64, 88, 255), inkDeep(22, 20, 86), sheen(132, 210, 255), paper(252, 252, 255),
        paperShade(214, 222, 250), band(200, 212, 240);
    auto tint = [](QColor color, qreal alpha) { color.setAlpha(int(std::clamp(alpha, 0.0, 255.0))); return color; };
    auto mix = [](const QColor &a, const QColor &b, qreal t) {
        return QColor::fromRgbF(a.redF() + (b.redF() - a.redF()) * t, a.greenF() + (b.greenF() - a.greenF()) * t,
            a.blueF() + (b.blueF() - a.blueF()) * t);
    };
    constexpr qreal trailMs = 450, brushMs = 1100, splashMs = 550, dropMs = 600, dripMs = 2600;
    const qint64 now = m_clock.elapsed();
    const bool pressed = !m_buttons.isEmpty();
    const QPointF shown = shownPosition(now);
    if (now - m_glideStart < m_glideMs + 16) m_trail.append({now, shown, pressed});
    while (!m_trail.isEmpty() && now - m_trail.front().time > (m_trail.front().pressed ? brushMs : trailMs)) {
        m_trail.removeFirst();
    }
    m_drops.removeIf([&](const InkDrop &drop) { return now - drop.born >= dropMs; });
    const bool splashing = now - m_lastClick < splashMs;
    const int interval = now - m_lastMove < brushMs || splashing || !m_drops.isEmpty() || pressed ? 16 : 50;
    if (m_animation.interval() != interval) m_animation.setInterval(interval);
    auto dropAt = [&](const InkDrop &drop) {
        const qreal age = (now - drop.born) / dropMs;
        return std::pair{drop.origin + drop.velocity * ((1 - std::pow(1 - age, 2)) * .35), drop.radius * std::pow(1 - age, .6)};
    };

    QRectF bounds(shown + QPointF(-24, -46), shown + QPointF(46, 30));
    for (const auto &sample : std::as_const(m_trail)) bounds |= QRectF(sample.point - QPointF(12, 12), QSizeF(24, 24));
    if (splashing) bounds |= QRectF(m_clickPosition - QPointF(46, 46), QSizeF(92, 92));
    for (const auto &drop : std::as_const(m_drops)) bounds |= QRectF(dropAt(drop).first - QPointF(12, 12), QSizeF(24, 24));
    bounds.adjust(-12, -12, 12, 12);
    // Bound allocation for large warps. A warp is not a visual sweep across the desktop.
    if (bounds.width() > 520 || bounds.height() > 520) {
        m_trail.clear();
        m_drops.clear();
        bounds = QRectF(shown + QPointF(-36, -58), shown + QPointF(58, 42));
    }
    bounds = bounds.toAlignedRect();
    const qreal scale = kwinApp()->devicePixelRatio();
    QImage image(QSize(qCeil(bounds.width() * scale), qCeil(bounds.height() * scale)), QImage::Format_ARGB32_Premultiplied);
    image.setDevicePixelRatio(scale);
    image.fill(Qt::transparent);
    QPainter p(&image);
    p.setRenderHint(QPainter::Antialiasing);
    p.translate(-bounds.topLeft());

    // A tapered ink stroke from the nib with a light core; its bloom keeps it readable on dark apps.
    for (qsizetype i = 1; i < m_trail.size(); ++i) {
        const InkSample &a = m_trail[i - 1], &b = m_trail[i];
        const qreal fade = std::max(0.0, 1.0 - (now - a.time) / (a.pressed ? brushMs : trailMs));
        if (fade <= 0 || a.point == b.point) continue;
        const qreal width = .7 + (a.pressed ? 8.5 : 6.5) * std::pow(fade, .8);
        p.setPen(QPen(tint(ink, 22 * fade), width * 3.2, Qt::SolidLine, Qt::RoundCap));
        p.drawLine(a.point, b.point);
        p.setPen(QPen(tint(ink, 40 * fade), width * 2.1, Qt::SolidLine, Qt::RoundCap));
        p.drawLine(a.point, b.point);
        p.setPen(QPen(tint(ink, 225 * std::pow(fade, 1.2)), width, Qt::SolidLine, Qt::RoundCap));
        p.drawLine(a.point, b.point);
        p.setPen(QPen(tint(sheen, 200 * std::pow(fade, 1.5)), width * .28, Qt::SolidLine, Qt::RoundCap));
        p.drawLine(a.point, b.point);
    }
    p.setPen(Qt::NoPen);
    for (const auto &drop : std::as_const(m_drops)) {
        const auto [center, radius] = dropAt(drop);
        p.setBrush(tint(ink, 215));
        p.drawEllipse(center, radius, radius);
    }
    if (splashing) {
        const qreal q = (now - m_lastClick) / splashMs;
        const qreal radius = 4 + 26 * (1 - std::pow(1 - q, 3)), wobble = .16 * (1 - q);
        QPainterPath ring;
        for (int i = 0; i <= 64; ++i) {
            const qreal angle = i * 2 * std::numbers::pi / 64;
            const qreal r = radius * (1 + wobble * std::sin(m_clickLobes * angle + m_clickPhase));
            const QPointF point = m_clickPosition + QPointF(std::cos(angle) * r, std::sin(angle) * r);
            i ? ring.lineTo(point) : ring.moveTo(point);
        }
        const qreal ringWidth = .8 + 2.6 * (1 - q);
        p.strokePath(ring, QPen(tint(ink, 235 * std::pow(1 - q, 1.6)), ringWidth));
        p.strokePath(ring, QPen(tint(sheen, 160 * std::pow(1 - q, 2)), ringWidth / 3));
        const qreal blot = 5.5 * std::pow(1 - q, .7);
        p.setPen(Qt::NoPen);
        p.setBrush(tint(ink, 190 * (1 - q)));
        p.drawEllipse(m_clickPosition, blot, blot);
    }
    // At rest a drop gathers on the nib, swells and falls.
    if (now - m_lastMove >= 400 && !pressed) {
        const qreal q = std::fmod(qreal(now - m_lastMove), dripMs) / dripMs;
        const qreal swell = std::sin(std::min(q / .8, 1.0) * std::numbers::pi / 2), fall = std::max(0.0, (q - .8) / .2);
        const qreal r = (1.2 + 1.8 * swell) * (1 - fall);
        if (r > .1) {
            const QPointF center = shown + QPointF(0, 1.5 + 10 * fall * fall);
            p.setBrush(tint(ink, 230 * (1 - fall)));
            p.drawEllipse(QRectF(center.x() - r, center.y() - r * 1.15, 2 * r, 2.15 * r));
        }
    }

    qreal targetLean = 0;
    if (m_trail.size() > 1) {
        targetLean = std::clamp((shown.x() - m_trail.front().point.x()) * .1, -8.0, 8.0);
    }
    m_lean += (targetLean - m_lean) * .25;
    // Pen parts as (along, across) the pen from the nib tip, which is the exact hotspot.
    const QPolygonF nib({{0, 0}, {3, -1.2}, {6.5, -2.6}, {10, -3.8}, {12.5, -4.2}, {12.5, 4.2}, {10, 3.8}, {6.5, 2.6}, {3, 1.2}});
    const QPolygonF nibShade({{0, 0}, {12.5, 0}, {12.5, 4.2}, {10, 3.8}, {6.5, 2.6}, {3, 1.2}});
    const QPolygonF section({{12.5, -4.4}, {17, -4.7}, {17, 4.7}, {12.5, 4.4}});
    const QPolygonF bandRing({{17, -5.1}, {18.6, -5.1}, {18.6, 5.1}, {17, 5.1}});
    const QPolygonF barrel({{18.6, -5.2}, {36, -5.2}, {38, -3.9}, {38.8, 0}, {38, 3.9}, {36, 5.2}, {18.6, 5.2}});
    const QPolygonF barrelShade({{18.6, .8}, {36.4, .8}, {38.3, 2.6}, {38, 3.9}, {36, 5.2}, {18.6, 5.2}});
    const QPolygonF barrelSheen({{19.6, -3.7}, {34.6, -3.7}, {34.6, -2.2}, {19.6, -2.2}});
    QPainterPath silhouette;
    silhouette.addPolygon(QPolygonF({{0, 0}, {3, -1.2}, {6.5, -2.6}, {10, -3.8}, {12.5, -4.2}, {12.5, -4.4}, {17, -4.7},
        {17, -5.1}, {18.6, -5.2}, {36, -5.2}, {38, -3.9}, {38.8, 0}, {38, 3.9}, {36, 5.2}, {18.6, 5.2}, {17, 5.1},
        {17, 4.7}, {12.5, 4.4}, {12.5, 4.2}, {10, 3.8}, {6.5, 2.6}, {3, 1.2}}));
    silhouette.closeSubpath();
    const qreal size = 1.15 * (pressed ? .9 : 1); // The pen pushes into the page.
    p.save();
    p.translate(shown);
    p.rotate(-50 + m_lean); // Rotate around the exact input hotspot, the nib tip.
    p.scale(size, size);
    const qreal energy = now - m_lastMove < 200 || pressed ? 1.0 : .7;
    p.setBrush(Qt::NoBrush);
    for (int width = 10; width >= 3; width -= 2) {
        p.setPen(QPen(tint(ink, (12 - width) * energy * 4), width / size, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
        p.drawPath(silhouette);
    }
    p.setPen(Qt::NoPen);
    p.setBrush(ink); p.drawPolygon(barrel);
    p.setBrush(mix(ink, inkDeep, .45)); p.drawPolygon(barrelShade);
    p.setBrush(mix(ink, Qt::white, .55)); p.drawPolygon(barrelSheen);
    p.setBrush(inkDeep); p.drawPolygon(section);
    p.setBrush(band); p.drawPolygon(bandRing);
    p.setBrush(paper); p.drawPolygon(nib);
    p.setBrush(paperShade); p.drawPolygon(nibShade);
    p.setBrush(Qt::NoBrush);
    const qreal line = 1.1 / size;
    p.setPen(QPen(tint(inkDeep, 235), line, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
    p.drawPath(silhouette);
    p.drawLine(QPointF(1.4, 0), QPointF(8.2, 0)); // The nib slit and its breather hole.
    p.setPen(Qt::NoPen);
    p.setBrush(tint(inkDeep, 230));
    p.drawEllipse(QPointF(8.9, 0), .9, .9);
    p.setPen(QPen(band, 1.6 / size, Qt::SolidLine, Qt::RoundCap));
    p.drawLine(QPointF(22, -6.1), QPointF(33.5, -6.1)); // The clip.
    p.setPen(Qt::NoPen);
    p.setBrush(band);
    p.drawEllipse(QPointF(33.5, -6.1), 1.2, 1.2);
    p.setPen(QPen(mix(ink, sheen, .7), line, Qt::SolidLine, Qt::RoundCap));
    p.drawLine(QPointF(19, -5.2), QPointF(36, -5.2));
    p.restore();
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
