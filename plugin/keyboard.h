// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <QJsonObject>
#include <QList>
#include <QPointer>
#include <functional>
#include <xkbcommon/xkbcommon.h>

struct wl_resource;
namespace KWin {
class ClientConnection;
class SurfaceInterface;

// Protocol delivery only: never changes KWin's focus, pressed keys or XKB state.
// The caller owns target visibility, connection ownership and takeover checks.
class ArtistKeyboard {
public:
    ~ArtistKeyboard();
    bool begin(SurfaceInterface *surface);
    bool ready() const;
    bool initialized() const { return m_state != nullptr; }
    bool key(uint32_t code, bool pressed, uint32_t time);
    void cancel(uint32_t time);
    void end(uint32_t time);
    int heldCount() const { return m_keys.size(); }
    static QList<uint32_t> resources(ClientConnection *client);
    // Printable characters reachable on the current layout, each as the
    // physical modifier keys then the key that produce it from a clean state.
    static QJsonObject characters();
private:
    void send(const std::function<void(wl_resource *)> &callback) const;
    void modifiers() const;
    QPointer<ClientConnection> m_client;
    QPointer<SurfaceInterface> m_surface;
    QList<uint32_t> m_resources;
    QList<uint32_t> m_keys;
    xkb_keymap *m_keymap = nullptr;
    xkb_state *m_state = nullptr;
};
}
