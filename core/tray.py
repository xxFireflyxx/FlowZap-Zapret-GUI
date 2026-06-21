"""
core/tray.py
------------
Иконка FlowZap в системном трее.
Требует: pip install pystray pillow
"""

import threading
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def _create_icon_image(color: str = "#00b4d8") -> "Image":
    """Создать иконку программно через Pillow."""
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    draw.ellipse([2, 2, size - 2, size - 2], fill=(r, g, b, 255))
    draw.line([18, 18, 46, 18], fill=(0, 0, 0, 255), width=5)
    draw.line([46, 18, 18, 46], fill=(0, 0, 0, 255), width=5)
    draw.line([18, 46, 46, 46], fill=(0, 0, 0, 255), width=5)
    return img


class TrayManager:

    def __init__(
        self,
        on_show:           Callable,
        on_toggle:         Callable,
        on_quit:           Callable,
        on_dns:            Callable,
        on_tg_proxy:       Callable = lambda: None,
        on_tg_open:        Callable = lambda: None,
        is_running_fn:     Callable[[], bool] = lambda: False,
        is_dns_on_fn:      Callable[[], bool] = lambda: False,
        is_tg_running_fn:  Callable[[], bool] = lambda: False,
        tg_available_fn:   Callable[[], bool] = lambda: False,
    ) -> None:
        self._on_show       = on_show
        self._on_toggle     = on_toggle
        self._on_quit       = on_quit
        self._on_dns        = on_dns
        self._on_tg_proxy   = on_tg_proxy
        self._on_tg_open    = on_tg_open
        self._is_running    = is_running_fn
        self._is_dns_on     = is_dns_on_fn
        self._is_tg_running = is_tg_running_fn
        self._tg_available  = tg_available_fn
        self._icon: Optional["pystray.Icon"] = None
        self._started    = False  # флаг — трей уже запущен

    def start(self) -> None:
        """Запустить трей. Если уже запущен — ничего не делать."""
        if self._started:
            logger.debug("Трей уже запущен, повторный запуск пропущен")
            return

        try:
            import pystray
        except ImportError:
            logger.warning("pystray не установлен — трей недоступен.")
            return

        self._started = True
        thread = threading.Thread(target=self._run_with_retry, daemon=True, name="tray")
        thread.start()

    def _run_with_retry(self, max_attempts: int = 5, delay: float = 2.0) -> None:
        """Пытаться поднять трей несколько раз — сразу после логина в Windows
        shell (explorer.exe) может быть ещё не готов принимать иконки,
        из-за чего pystray падает или создаёт "призрачную" неотвечающую иконку."""
        import time
        for attempt in range(1, max_attempts + 1):
            ok = self._run()
            if ok:
                return
            logger.warning(f"Трей не поднялся (попытка {attempt}/{max_attempts}), повтор через {delay}с")
            time.sleep(delay)
        logger.error("Не удалось запустить трей после всех попыток")
        self._started = False

    def _run(self) -> bool:
        try:
            import pystray

            def _toggle(icon, item):
                self._on_toggle()
                self._update_menu()

            def _show(icon, item):
                self._on_show()

            def _quit(icon, item):
                icon.stop()
                self._on_quit()

            def _toggle_label(item):
                return "■ Остановить zapret" if self._is_running() else "▶ Запустить zapret"

            def _dns_label(item):
                return "🔴 Выключить DNS" if self._is_dns_on() else "🟢 Включить DNS"

            def _dns_action(icon, item):
                self._on_dns()
                self._update_menu()

            def _tg_label(item):
                if not self._tg_available():
                    return "TG Proxy (не установлен)"
                return "🔴 Выключить TG Proxy" if self._is_tg_running() else "🟢 Включить TG Proxy"

            def _tg_action(icon, item):
                self._on_tg_proxy()
                self._update_menu()

            def _tg_open(icon, item):
                self._on_tg_open()

            def _tg_enabled(item):
                return self._tg_available()

            menu = pystray.Menu(
                pystray.MenuItem("FlowZap", _show, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(_toggle_label, _toggle),
                pystray.MenuItem(_dns_label, _dns_action),
                pystray.MenuItem(_tg_label, _tg_action, enabled=_tg_enabled),
                pystray.MenuItem("📱 Открыть в Telegram", _tg_open, enabled=_tg_enabled),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Выход", _quit),
            )

            self._icon = pystray.Icon(
                name="FlowZap",
                icon=_create_icon_image("#00b4d8"),
                title="FlowZap",
                menu=menu,
            )
            self._icon.run()
            # run() возвращается только после icon.stop() — штатное завершение
            return True
        except Exception as e:
            import traceback
            logger.error(f"Ошибка трея: {e}\n{traceback.format_exc()}")
            return False
        finally:
            self._started = False
            self._icon = None

    def update_icon(self, running: bool) -> None:
        if not self._icon:
            return
        try:
            color = "#22c55e" if running else "#00b4d8"
            self._icon.icon = _create_icon_image(color)
            self._icon.title = "FlowZap — Активен" if running else "FlowZap — Остановлен"
        except Exception as e:
            logger.debug(f"Ошибка обновления иконки трея: {e}")

    def _update_menu(self) -> None:
        if self._icon:
            try:
                self._icon.update_menu()
            except Exception:
                pass

    def stop(self) -> None:
        """Остановить трей и сбросить флаг."""
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
        self._started = False
        self._icon = None
