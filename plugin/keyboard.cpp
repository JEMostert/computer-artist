// SPDX-License-Identifier: GPL-2.0-or-later
#include "keyboard.h"
#include <input.h>
#include <keyboard_input.h>
#include <xkb.h>
#include <wayland_server.h>
#include <wayland/clientconnection.h>
#include <wayland/display.h>
#include <wayland/seat.h>
#include <wayland/surface.h>
#include <wayland-server-core.h>
#include <wayland-server-protocol.h>
#include <algorithm>
#include <cstring>

namespace KWin {
ArtistKeyboard::~ArtistKeyboard() { end(0); }
QList<uint32_t> ArtistKeyboard::resources(ClientConnection *client) {
    QList<uint32_t> result;
    if (!client || client->tearingDown()) return result;
    wl_client_for_each_resource(client->client(), [](wl_resource *resource, void *data) {
        if (std::strcmp(wl_resource_get_class(resource), "wl_keyboard") == 0)
            static_cast<QList<uint32_t> *>(data)->append(wl_resource_get_id(resource));
        return WL_ITERATOR_CONTINUE;
    }, &result);
    std::sort(result.begin(), result.end());
    return result;
}
void ArtistKeyboard::send(const std::function<void(wl_resource *)> &callback) const {
    if (!m_client || m_client->tearingDown()) return;
    for (auto id : m_resources) {
        auto resource = wl_client_get_object(m_client->client(), id);
        if (resource && std::strcmp(wl_resource_get_class(resource), "wl_keyboard") == 0)
            callback(resource);
    }
    m_client->flush();
}
bool ArtistKeyboard::begin(SurfaceInterface *surface) {
    if (m_client || !surface) return false;
    auto client = surface->client();
    const auto keyboards = resources(client);
    auto hostKeymap = input()->keyboard()->xkb()->keymap();
    if (keyboards.isEmpty() || !hostKeymap) return false;
    auto state = xkb_state_new(hostKeymap);
    if (!state) return false;
    m_keymap = xkb_keymap_ref(hostKeymap);
    m_state = state;
    // Match the current layout, with independent modifier and lock state.
    xkb_state_update_mask(m_state, 0, 0, 0, 0, 0, input()->keyboard()->xkb()->currentLayout());
    m_client = client;
    m_surface = surface;
    m_resources = keyboards;
    wl_array keys;
    wl_array_init(&keys);
    const auto serial = waylandServer()->display()->nextSerial();
    send([&](wl_resource *r) { wl_keyboard_send_enter(r, serial, surface->resource(), &keys); });
    wl_array_release(&keys);
    modifiers();
    return true;
}
bool ArtistKeyboard::ready() const {
    return m_client && !m_client->tearingDown() && m_surface && m_state
        && input()->keyboard()->xkb()->keymap() == m_keymap
        && resources(m_client) == m_resources;
}
void ArtistKeyboard::modifiers() const {
    if (!m_state) return;
    auto serial = waylandServer()->display()->nextSerial();
    send([&](wl_resource *r) {
        wl_keyboard_send_modifiers(r, serial,
            xkb_state_serialize_mods(m_state, XKB_STATE_MODS_DEPRESSED),
            xkb_state_serialize_mods(m_state, XKB_STATE_MODS_LATCHED),
            xkb_state_serialize_mods(m_state, XKB_STATE_MODS_LOCKED),
            xkb_state_serialize_layout(m_state, XKB_STATE_LAYOUT_EFFECTIVE));
    });
}
bool ArtistKeyboard::key(uint32_t code, bool pressed, uint32_t time) {
    if (!ready() || code == 0 || code > 247 || m_keys.contains(code) == pressed) return false;
    if (pressed) m_keys.append(code); else m_keys.removeAll(code);
    xkb_state_update_key(m_state, code + 8, pressed ? XKB_KEY_DOWN : XKB_KEY_UP);
    auto serial = waylandServer()->display()->nextSerial();
    send([&](wl_resource *r) { wl_keyboard_send_key(r, serial, time, code,
        pressed ? WL_KEYBOARD_KEY_STATE_PRESSED : WL_KEYBOARD_KEY_STATE_RELEASED); });
    modifiers();
    return true;
}
void ArtistKeyboard::cancel(uint32_t time) {
    // Cleanup must work even after resource/keymap readiness changes.
    for (auto it = m_keys.crbegin(); it != m_keys.crend(); ++it) {
        const auto code = *it;
        if (m_state) xkb_state_update_key(m_state, code + 8, XKB_KEY_UP);
        auto serial = waylandServer()->display()->nextSerial();
        send([&](wl_resource *r) { wl_keyboard_send_key(r, serial, time, code, WL_KEYBOARD_KEY_STATE_RELEASED); });
    }
    m_keys.clear();
    // Clear agent locks too; they must not leak into the human's next enter.
    if (m_state) {
        const auto layout = xkb_state_serialize_layout(m_state, XKB_STATE_LAYOUT_EFFECTIVE);
        xkb_state_update_mask(m_state, 0, 0, 0, 0, 0, layout);
    }
    modifiers();
}
void ArtistKeyboard::end(uint32_t time) {
    if (!m_state && !m_client) return;
    cancel(time);
    const auto human = waylandServer()->seat()->focusedKeyboardSurface();
    if (m_surface && (!human || human->client() != m_client)) {
        auto serial = waylandServer()->display()->nextSerial();
        send([&](wl_resource *r) { wl_keyboard_send_leave(r, serial, m_surface->resource()); });
    }
    m_surface.clear(); m_client.clear(); m_resources.clear();
    if (m_state) xkb_state_unref(m_state);
    if (m_keymap) xkb_keymap_unref(m_keymap);
    m_state = nullptr; m_keymap = nullptr;
}
}
