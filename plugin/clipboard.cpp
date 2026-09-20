// SPDX-License-Identifier: GPL-2.0-or-later
#include "clipboard.h"
#include "data-control-client.h"
#include <QHash>
#include <QSocketNotifier>
#include <QStringDecoder>
#include <QTimer>
#include <wayland-client.h>
#include <sys/socket.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <cerrno>

namespace {
QJsonObject failure(const char *error) { return {{"ok",false},{"error",error}}; }

// Clipboard receivers supply pipes. Block SIGPIPE only for this write, and
// consume a newly generated signal before restoring the caller's signal mask.
ssize_t pipeWrite(int fd, const char *data, size_t size) {
    sigset_t blocked, old, pending;
    sigemptyset(&blocked); sigaddset(&blocked,SIGPIPE);
    pthread_sigmask(SIG_BLOCK,&blocked,&old);
    sigpending(&pending);
    const auto result=::write(fd,data,size);
    const int saved=errno;
    if(result<0 && saved==EPIPE && !sigismember(&pending,SIGPIPE)) {
        timespec zero{};
        sigtimedwait(&blocked,nullptr,&zero);
    }
    pthread_sigmask(SIG_SETMASK,&old,nullptr);
    errno=saved;
    return result;
}

class Transfer : public QObject {
public:
    int fd;
    QByteArray bytes;
    QSocketNotifier *notifier;
    QTimer timer;
    Clipboard::Reply reply;
    bool writing, done=false;
    Transfer(int descriptor, QByteArray data, bool output, QObject *parent, Clipboard::Reply callback = {})
        : QObject(parent), fd(descriptor), bytes(data), reply(std::move(callback)), writing(output) {
        fcntl(fd,F_SETFL,fcntl(fd,F_GETFL)|O_NONBLOCK);
        notifier=new QSocketNotifier(fd,output?QSocketNotifier::Write:QSocketNotifier::Read,this);
        connect(notifier,&QSocketNotifier::activated,this,[this] { pump(); });
        timer.setSingleShot(true);
        connect(&timer,&QTimer::timeout,this,[this] { finish(failure("clipboard_timeout")); });
        timer.start(1500);
    }
    ~Transfer() override { if(fd>=0) ::close(fd); }
    void finish(QJsonObject result) {
        if(done) return;
        done=true; notifier->setEnabled(false); timer.stop();
        ::close(fd); fd=-1;
        if(reply) reply(result);
        deleteLater();
    }
    void pump() {
        if(writing) {
            const auto n=pipeWrite(fd,bytes.constData(),bytes.size());
            if(n>0) bytes.remove(0,n);
            if(bytes.isEmpty()) finish({{"ok",true}});
            else if(n<0 && errno!=EAGAIN && errno!=EINTR) finish(failure("clipboard_write_failed"));
        } else {
            char buffer[4096];
            for(;;) {
                const auto n=::read(fd,buffer,sizeof(buffer));
                if(n>0) {
                    bytes.append(buffer,n);
                    if(bytes.size()>Clipboard::MaxBytes) { finish(failure("clipboard_text_too_large")); return; }
                } else if(n==0) {
                    QStringDecoder decoder(QStringDecoder::Utf8);
                    QString text=decoder(bytes);
                    finish(decoder.hasError()?failure("clipboard_invalid_utf8"):QJsonObject{{"ok",true},{"text",text}});
                    return;
                } else {
                    if(errno==EINTR) continue;
                    if(errno!=EAGAIN) finish(failure("clipboard_read_failed"));
                    return;
                }
            }
        }
    }
};
}

struct Clipboard::State {
    Clipboard *owner;
    wl_display *display=nullptr;
    wl_registry *registry=nullptr;
    wl_seat *seat=nullptr;
    ext_data_control_manager_v1 *manager=nullptr;
    ext_data_control_device_v1 *device=nullptr;
    ext_data_control_offer_v1 *selection=nullptr;
    QHash<ext_data_control_offer_v1 *, QStringList> offers;
    QHash<ext_data_control_source_v1 *, QByteArray> sources;
    QHash<wl_callback *, Reply> syncs;
    QSocketNotifier *reader=nullptr, *writer=nullptr;
    bool initialized=false, failed=false;
    quint64 revision=0;

    void flush() {
        if(failed) return;
        if(wl_display_flush(display)<0) {
            if(errno==EAGAIN) writer->setEnabled(true);
            else fail();
        } else writer->setEnabled(false);
    }
    void fail() {
        failed=true; initialized=false;
        reader->setEnabled(false); writer->setEnabled(false);
        const auto pending=syncs; syncs.clear();
        for(auto i=pending.begin();i!=pending.end();++i) {
            wl_callback_destroy(i.key()); i.value()(failure("clipboard_disconnected"));
        }
    }
    void sync(Reply reply) {
        auto callback=wl_display_sync(display);
        auto completed=std::make_shared<bool>(false);
        syncs.insert(callback,[completed,reply=std::move(reply)](QJsonObject result) {
            *completed=true; reply(result);
        });
        static const wl_callback_listener listener={[](void *data,wl_callback *cb,uint32_t) {
            auto s=static_cast<State *>(data);
            auto reply=s->syncs.take(cb); wl_callback_destroy(cb);
            if(reply) reply({{"ok",true}});
        }};
        wl_callback_add_listener(callback,&listener,this);
        QTimer::singleShot(1500,owner,[this,completed] {
            // A timed-out connection cannot safely complete subsequent writes.
            if(!*completed) fail();
        });
        flush();
    }
    void bindDevice() {
        if(!seat || !manager || device) return;
        device=ext_data_control_manager_v1_get_data_device(manager,seat);
        static const ext_data_control_device_v1_listener listener={
            [](void *data,ext_data_control_device_v1 *,ext_data_control_offer_v1 *offer) {
                auto s=static_cast<State *>(data); s->offers.insert(offer,{});
                static const ext_data_control_offer_v1_listener offerListener={
                    [](void *data,ext_data_control_offer_v1 *offer,const char *mime) {
                        static_cast<State *>(data)->offers[offer].append(QString::fromUtf8(mime));
                    }};
                ext_data_control_offer_v1_add_listener(offer,&offerListener,s);
            },
            [](void *data,ext_data_control_device_v1 *,ext_data_control_offer_v1 *offer) {
                auto s=static_cast<State *>(data);
                if(s->selection) { s->offers.remove(s->selection); ext_data_control_offer_v1_destroy(s->selection); }
                s->selection=offer; s->initialized=true; ++s->revision;
            },
            [](void *data,ext_data_control_device_v1 *) { static_cast<State *>(data)->fail(); },
            [](void *data,ext_data_control_device_v1 *,ext_data_control_offer_v1 *offer) {
                if(offer) { static_cast<State *>(data)->offers.remove(offer); ext_data_control_offer_v1_destroy(offer); }
            }
        };
        ext_data_control_device_v1_add_listener(device,&listener,this);
        flush();
    }
    ~State() {
        delete reader; delete writer;
        for(auto cb:syncs.keys()) wl_callback_destroy(cb);
        for(auto source:sources.keys()) ext_data_control_source_v1_destroy(source);
        for(auto offer:offers.keys()) ext_data_control_offer_v1_destroy(offer);
        if(device) ext_data_control_device_v1_destroy(device);
        if(manager) ext_data_control_manager_v1_destroy(manager);
        if(seat) wl_seat_destroy(seat);
        if(registry) wl_registry_destroy(registry);
        if(display) { wl_display_flush(display); wl_display_disconnect(display); }
    }
};

Clipboard::Clipboard(const QString &name,QObject *parent) : QObject(parent),d(std::make_unique<State>()) {
    d->owner=this;
    d->display=wl_display_connect(name.toUtf8().constData());
    if(!d->display) return;
    const int fd=wl_display_get_fd(d->display);
    d->reader=new QSocketNotifier(fd,QSocketNotifier::Read,this);
    d->writer=new QSocketNotifier(fd,QSocketNotifier::Write,this);
    d->writer->setEnabled(false);
    connect(d->writer,&QSocketNotifier::activated,this,[this] { d->flush(); });
    connect(d->reader,&QSocketNotifier::activated,this,[this] {
        // Only this event loop reads the connection; no blocking round trips.
        if(wl_display_dispatch(d->display)<0) d->fail();
        else d->flush();
    });
    d->registry=wl_display_get_registry(d->display);
    static const wl_registry_listener listener={
        [](void *data,wl_registry *registry,uint32_t name,const char *interface,uint32_t) {
            auto s=static_cast<State *>(data);
            if(QString::fromLatin1(interface)==QStringLiteral("wl_seat") && !s->seat)
                s->seat=static_cast<wl_seat *>(wl_registry_bind(registry,name,&wl_seat_interface,1));
            if(QString::fromLatin1(interface)==QStringLiteral("ext_data_control_manager_v1"))
                s->manager=static_cast<ext_data_control_manager_v1 *>(wl_registry_bind(registry,name,&ext_data_control_manager_v1_interface,1));
            s->bindDevice();
        },
        [](void *,wl_registry *,uint32_t) {}
    };
    wl_registry_add_listener(d->registry,&listener,d.get());
    d->flush();
}
Clipboard::~Clipboard() = default;
bool Clipboard::ready() const { return d->initialized && !d->failed; }

void Clipboard::write(const QString &text,Reply reply) {
    if(!ready()) { reply(failure("clipboard_unavailable")); return; }
    const auto bytes=text.toUtf8();
    if(bytes.size()>MaxBytes) { reply(failure("clipboard_text_too_large")); return; }
    auto source=ext_data_control_manager_v1_create_data_source(d->manager);
    d->sources.insert(source,bytes);
    static const ext_data_control_source_v1_listener listener={
        [](void *data,ext_data_control_source_v1 *source,const char *mime,int32_t fd) {
            auto s=static_cast<State *>(data);
            if(QByteArray(mime)!="text/plain;charset=utf-8" && QByteArray(mime)!="text/plain") { ::close(fd); return; }
            new Transfer(fd,s->sources.value(source),true,s->owner);
        },
        [](void *data,ext_data_control_source_v1 *source) {
            static_cast<State *>(data)->sources.remove(source);
            ext_data_control_source_v1_destroy(source);
        }
    };
    ext_data_control_source_v1_add_listener(source,&listener,d.get());
    ext_data_control_source_v1_offer(source,"text/plain;charset=utf-8");
    ext_data_control_source_v1_offer(source,"text/plain");
    ext_data_control_device_v1_set_selection(d->device,source);
    d->sync(std::move(reply));
}

void Clipboard::read(Reply reply) {
    if(!ready()) { reply(failure("clipboard_unavailable")); return; }
    // Observe selection changes already queued on the server before choosing an offer.
    d->sync([this,reply=std::move(reply)](QJsonObject result) {
        if(!result.value("ok").toBool()) { reply(result); return; }
        if(!d->selection) { reply({{"ok",true},{"text",QString()}}); return; }
        QString mime;
        const auto types=d->offers.value(d->selection);
        for(const auto &candidate:{QStringLiteral("text/plain;charset=utf-8"),QStringLiteral("text/plain")})
            if(types.contains(candidate)) { mime=candidate; break; }
        if(mime.isEmpty()) { reply(failure("clipboard_has_no_utf8_text")); return; }
        int fds[2];
        if(socketpair(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0,fds)<0) { reply(failure("clipboard_pipe_failed")); return; }
        const auto revision=d->revision;
        new Transfer(fds[0],{},false,this,[this,revision,reply](QJsonObject result) {
            reply(revision==d->revision?result:failure("clipboard_changed_during_read"));
        });
        ext_data_control_offer_v1_receive(d->selection,mime.toUtf8().constData(),fds[1]);
        ::close(fds[1]); d->flush();
    });
}
