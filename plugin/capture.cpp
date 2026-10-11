// SPDX-License-Identifier: GPL-2.0-or-later
#include "capture.h"
#include <main.h>
#include <window.h>
#include <workspace.h>
#include <QSet>
#include <scene/workspacescene.h>
#include <scene/windowitem.h>
#include <scene/itemrenderer.h>
#include <effect/effect.h>
#include <core/rendertarget.h>
#include <core/renderviewport.h>
#include <opengl/eglcontext.h>
#include <opengl/glframebuffer.h>
#include <opengl/gltexture.h>

namespace KWin {
// Render the client rectangle, including subsurfaces and GPU-backed content.
// Reading only wl_surface.buffer misses SDL/libdecor child surfaces.
QImage captureClient(Window *window)
{
    auto scene = kwinApp()->scene();
    auto context = scene ? scene->openglContext() : nullptr;
    if (!scene || !scene->renderer() || !window->windowItem()) return {};
    QList<Window *> layers{window};
    // Keep the client coordinate system stable. Transients extending outside it
    // remain independently capturable by their IDs in windows().
    for (auto candidate : workspace()->stackingOrder()) {
        if (candidate==window || candidate->isDeleted() || !candidate->readyForPainting()
            || candidate->isMinimized() || candidate->isHidden() || candidate->isHiddenByShowDesktop()
            || !candidate->isOnCurrentDesktop() || !candidate->isOnCurrentActivity()
            || !candidate->windowItem()) continue;
        QSet<Window *> seen;
        for (auto parent=candidate->transientFor(); parent && !seen.contains(parent); parent=parent->transientFor()) {
            if (parent==window) { layers.append(candidate); break; }
            seen.insert(parent);
        }
    }
    const auto rect = window->clientGeometry();
    const auto size = (rect.size() * window->targetScale()).toSize();
    if (size.isEmpty() || qint64(size.width()) * size.height() > 64 * 1024 * 1024) return {};
    if (!context) {
        QImage image(size, QImage::Format_ARGB32_Premultiplied);
        image.fill(Qt::transparent);
        RenderTarget target(&image);
        RenderViewport viewport(rect, window->targetScale(), target, QPoint());
        auto renderer = scene->renderer();
        renderer->beginFrame(target, viewport);
        for (auto layer : layers) renderer->renderItem(target, viewport, layer->windowItem(), Scene::PAINT_WINDOW_TRANSFORMED,
                             Region::infinite(), WindowPaintData{}, {}, {});
        renderer->endFrame();
        return image;
    }
    auto previous = EglContext::currentContext();
    if (!context->makeCurrent()) return {};
    QImage image;
    {
        auto texture = GLTexture::allocate(GL_RGBA8, size);
        if (texture) {
            texture->setContentTransform(OutputTransform::FlipY);
            GLFramebuffer framebuffer(texture.get());
            if (framebuffer.valid()) {
                RenderTarget target(&framebuffer);
                RenderViewport viewport(rect, window->targetScale(), target, QPoint());
                auto renderer = scene->renderer();
                renderer->beginFrame(target, viewport);
                glClearColor(0, 0, 0, 0);
                glClear(GL_COLOR_BUFFER_BIT);
                for (auto layer : layers) renderer->renderItem(target, viewport, layer->windowItem(), Scene::PAINT_WINDOW_TRANSFORMED,
                                     Region::infinite(), WindowPaintData{}, {}, {});
                renderer->endFrame();
                image = texture->toImage();
            }
        }
    }
    if (previous && previous != context) {
        // Never leave the capture context current in place of KWin's own.
        if (!previous->makeCurrent()) context->doneCurrent();
    } else if (!previous) {
        context->doneCurrent();
    }
    return image;
}

}
