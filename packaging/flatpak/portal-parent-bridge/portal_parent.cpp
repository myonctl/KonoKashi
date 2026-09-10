// Minimal Qt-to-portal parent bridge for the pinned KDE 6.11 Flatpak runtime.

#include <QByteArray>
#include <QGuiApplication>
#include <QWindow>
#include <private/qdesktopunixservices_p.h>
#include <private/qguiapplication_p.h>
#include <qpa/qplatformintegration.h>

#include <cstddef>
#include <cstring>

#if defined(__GNUC__)
#define KONOKASHI_EXPORT __attribute__((visibility("default")))
#else
#define KONOKASHI_EXPORT
#endif

extern "C" KONOKASHI_EXPORT std::size_t
konokashi_portal_parent_identifier(QWindow *window, char *output,
                                    std::size_t capacity)
{
    if (window == nullptr || output == nullptr || capacity == 0)
        return 0;

    auto *integration = QGuiApplicationPrivate::platformIntegration();
    if (integration == nullptr)
        return 0;

    auto *services = dynamic_cast<QDesktopUnixServices *>(integration->services());
    if (services == nullptr)
        return 0;

    const QByteArray identifier = services->portalWindowIdentifier(window).toUtf8();
    const auto size = static_cast<std::size_t>(identifier.size());
    if (identifier.isEmpty() || size >= capacity)
        return 0;

    std::memcpy(output, identifier.constData(), size);
    output[size] = '\0';
    return size;
}
