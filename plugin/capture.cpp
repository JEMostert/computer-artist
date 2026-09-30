// SPDX-License-Identifier: GPL-2.0-or-later
#include "capture.h"
#include <main.h>
#include <window.h>
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
        renderer->renderItem(target, viewport, window->windowItem(), Scene::PAINT_WINDOW_TRANSFORMED,
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
                renderer->renderItem(target, viewport, window->windowItem(), Scene::PAINT_WINDOW_TRANSFORMED,
                                     Region::infinite(), WindowPaintData{}, {}, {});
                renderer->endFrame();
                image = texture->toImage();
            }
        }
    }
    if (previous && previous != context) {
        if (!previous->makeCurrent()) return {};
    } else if (!previous) {
        context->doneCurrent();
    }
    return image;
}

}
