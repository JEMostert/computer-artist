// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <QObject>
#include <QJsonObject>
#include <functional>
#include <memory>

// A nonblocking Wayland data-control client connected to this compositor.
// Owns text independently of CLI connections; never uses the primary selection.
class Clipboard : public QObject {
public:
    using Reply = std::function<void(QJsonObject)>;
    static constexpr int MaxBytes = 8192;
    explicit Clipboard(const QString &display, QObject *parent);
    ~Clipboard() override;
    bool ready() const;
    void read(Reply reply);
    void write(const QString &text, Reply reply);
private:
    struct State;
    std::unique_ptr<State> d;
};
