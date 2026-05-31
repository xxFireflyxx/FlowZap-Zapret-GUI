"""
ui/updates_tab.py
-----------------
Вкладка «Обновления».
Два блока: обновление FlowZap (GUI) и обновление Core (zapret).
"""

import threading
import customtkinter as ctk
from pathlib import Path
from ui.theme import theme
from core.updater import (
    GUI_VERSION, FLOWZAP_REPO,
    get_latest_release, find_exe_asset,
    get_installed_core_version, download_and_install_core,
    download_and_install_exe, RateLimitError,
)


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except Exception:
        return (0,)


CORE_REPO     = "Flowseal/zapret-discord-youtube"
TGPROXY_REPO  = "Flowseal/tg-ws-proxy"
TGPROXY_EXE   = "TgWsProxy_windows.exe"


class UpdatesTab(ctk.CTkFrame):
    def __init__(
        self,
        parent: ctk.CTkFrame,
        config: dict = None,
        manager=None,
        on_core_updated=None,
    ) -> None:
        p = theme.palette
        super().__init__(parent, fg_color=p.bg_root, corner_radius=0)
        self._config = config or {}
        self._manager = manager
        self._on_core_updated = on_core_updated
        # При запуске из exe Path(__file__) указывает на _internal — берём из конфига
        _cfg_app_dir = (config or {}).get("_app_dir")
        self._app_dir = Path(_cfg_app_dir) if _cfg_app_dir else Path(__file__).parent.parent
        self._latest_gui_release = None
        self._build()

    def _build(self) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text="Обновления",
            font=(t.family_ui, t.size_xl, "bold"),
            text_color=p.text_primary,
        ).grid(row=0, column=0, sticky="w", padx=m.padding_lg,
               pady=(m.padding_lg, m.padding_md))

        # ── Блок 1: FlowZap GUI ───────────────────
        self._build_gui_block(row=1)

        # ── Блок 2: Core (zapret) ─────────────────
        self._build_core_block(row=2)

        # ── Блок 3: TG WS Proxy ───────────────────
        self._build_tg_proxy_block(row=3)

    # ──────────────────────────────────────────────
    #  Блок FlowZap GUI
    # ──────────────────────────────────────────────

    def _build_gui_block(self, row: int) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        card = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        card.grid(row=row, column=0, sticky="ew", padx=m.padding_lg,
                  pady=(0, m.padding_md))
        card.grid_columnconfigure(0, weight=1)

        # Заголовок
        ctk.CTkLabel(card, text="FlowZap",
                     font=(t.family_ui, t.size_md, "bold"),
                     text_color=p.text_primary).grid(
            row=0, column=0, sticky="w", padx=m.padding_md,
            pady=(m.padding_md, 4))

        # Версия приложения
        ver_row = ctk.CTkFrame(card, fg_color="transparent")
        ver_row.grid(row=1, column=0, sticky="w", padx=m.padding_md, pady=(0, 4))

        ctk.CTkLabel(ver_row, text="Версия приложения:",
                     font=(t.family_ui, t.size_sm),
                     text_color=p.text_secondary).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(ver_row, text=f"v{GUI_VERSION}",
                     font=(t.family_ui, t.size_sm),
                     text_color=p.text_primary).pack(side="left")

        self._gui_status = ctk.CTkLabel(
            card, text="",
            font=(t.family_ui, t.size_sm), text_color=p.text_secondary)
        self._gui_status.grid(row=2, column=0, sticky="w",
                              padx=m.padding_md, pady=(0, 8))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=3, column=0, sticky="w",
                     padx=m.padding_md, pady=(0, m.padding_md))

        self._btn_gui_check = ctk.CTkButton(
            btn_row, text="Проверить",
            fg_color=p.bg_input, hover_color=p.bg_hover,
            text_color=p.text_primary, height=m.button_height,
            corner_radius=m.corner_radius,
            command=self._check_gui,
        )
        self._btn_gui_check.pack(side="left", padx=(0, 8))

        self._btn_gui_update = ctk.CTkButton(
            btn_row, text="Обновить",
            fg_color=p.accent, hover_color=p.accent_dim,
            text_color=p.bg_root, height=m.button_height,
            corner_radius=m.corner_radius,
            state="disabled",
            command=self._update_gui,
        )
        self._btn_gui_update.pack(side="left")

    # ──────────────────────────────────────────────
    #  Блок Core (zapret)
    # ──────────────────────────────────────────────

    def _build_core_block(self, row: int) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        card = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        card.grid(row=row, column=0, sticky="ew", padx=m.padding_lg,
                  pady=(0, m.padding_md))
        card.grid_columnconfigure(1, weight=1)

        # Заголовок
        ctk.CTkLabel(card, text="Core (zapret)",
                     font=(t.family_ui, t.size_md, "bold"),
                     text_color=p.text_primary).grid(
            row=0, column=0, columnspan=2, sticky="w",
            padx=m.padding_md, pady=(m.padding_md, 4))

        # Версия установленная
        inst_row = ctk.CTkFrame(card, fg_color="transparent")
        inst_row.grid(row=1, column=0, columnspan=2, sticky="w",
                      padx=m.padding_md, pady=(0, 2))
        ctk.CTkLabel(inst_row, text="Версия Core:",
                     font=(t.family_ui, t.size_sm),
                     text_color=p.text_secondary).pack(side="left", padx=(0, 6))
        installed = get_installed_core_version(self._app_dir / "zapret")
        self._core_installed_lbl = ctk.CTkLabel(
            inst_row,
            text=installed if installed else "не установлен",
            font=(t.family_ui, t.size_sm),
            text_color=p.text_primary)
        self._core_installed_lbl.pack(side="left")

        self._core_latest_lbl = ctk.CTkLabel(card, text="")  # скрыт

        self._core_status = ctk.CTkLabel(
            card, text="",
            font=(t.family_ui, t.size_xs), text_color=p.text_muted,
            anchor="w", wraplength=450)
        self._core_status.grid(row=2, column=0, columnspan=2,
                               padx=m.padding_md, pady=(0, 8), sticky="ew")

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=3, column=0, columnspan=2,
                     padx=m.padding_md, pady=(0, m.padding_md), sticky="w")

        self._btn_core_check = ctk.CTkButton(
            btn_row, text="Проверить",
            fg_color=p.bg_input, hover_color=p.bg_hover,
            text_color=p.text_primary, height=m.button_height,
            corner_radius=m.corner_radius,
            command=self._check_core,
        )
        self._btn_core_check.pack(side="left", padx=(0, 8))

        self._btn_core_update = ctk.CTkButton(
            btn_row, text="Обновить",
            fg_color=p.accent, hover_color=p.accent_dim,
            text_color=p.bg_root, height=m.button_height,
            corner_radius=m.corner_radius,
            state="disabled",
            command=self._update_core,
        )
        self._btn_core_update.pack(side="left")

    # ──────────────────────────────────────────────
    #  GUI обновление
    # ──────────────────────────────────────────────

    def _enable_gui_update(self, enable: bool) -> None:
        p = theme.palette
        self._gui_update_allowed = enable
        if enable:
            self._btn_gui_update.configure(
                state="normal",
                fg_color=p.accent, hover_color=p.accent_dim,
                text_color=p.bg_root)
        else:
            self._btn_gui_update.configure(
                state="disabled",
                fg_color=p.bg_hover, hover_color=p.bg_hover,
                text_color=p.text_secondary)

    def _enable_core_update(self, enable: bool) -> None:
        p = theme.palette
        self._core_update_allowed = enable
        if enable:
            self._btn_core_update.configure(
                state="normal",
                fg_color=p.accent, hover_color=p.accent_dim,
                text_color=p.bg_root)
        else:
            self._btn_core_update.configure(
                state="disabled",
                fg_color=p.bg_hover, hover_color=p.bg_hover,
                text_color=p.text_secondary)

    def _check_gui(self) -> None:
        self._btn_gui_check.configure(state="disabled", text="Проверка…")
        self._gui_status.configure(text="Проверка обновлений…",
                                   text_color=theme.palette.text_secondary)
        def _worker():
            try:
                release = get_latest_release()
                self.after(0, self._apply_gui_check, release)
            except RateLimitError as e:
                _msg = str(e)
                self.after(0, lambda m=_msg: (
                    self._btn_gui_check.configure(state="normal", text="Проверить"),
                    self._gui_status.configure(text=m, text_color=theme.palette.warning)
                ))
        threading.Thread(target=_worker, daemon=True).start()

    def _apply_gui_check(self, release) -> None:
        p = theme.palette
        self._btn_gui_check.configure(state="normal", text="Проверить")
        if not release:
            self._gui_status.configure(
                text="Не удалось подключиться к GitHub.",
                text_color=p.error)
            return

        self._latest_gui_release = release
        # Сохраняем в кэш чтобы on_activate восстановил статус
        try:
            root = self.winfo_toplevel()
            if not hasattr(root, "_update_cache"):
                root._update_cache = {}
            root._update_cache["gui"] = release
        except Exception:
            pass
        tag = release.get("tag_name", "?")
        has_asset = find_exe_asset(release) is not None
        current = _version_tuple(GUI_VERSION)
        latest = _version_tuple(tag)

        if latest > current:
            if has_asset:
                self._gui_status.configure(
                    text=f"Доступна новая версия: {tag}",
                    text_color=p.success)
                self._enable_gui_update(True)
            else:
                self._gui_status.configure(
                    text=f"Версия {tag} есть, но файл релиза ещё не добавлен.",
                    text_color=p.warning)
        else:
            self._gui_status.configure(
                text=f"У вас актуальная версия ({tag})",
                text_color=p.success)

    def _update_gui(self) -> None:
        self._enable_gui_update(False)
        self._btn_gui_check.configure(state="disabled")
        download_and_install_exe(
            install_dir=self._app_dir,
            on_progress=lambda msg: self.after(
                0, lambda m=msg: self._gui_status.configure(text=m)),
            on_done=lambda ok, msg: self.after(0, self._on_gui_done, ok, msg),
        )

    def _on_gui_done(self, success: bool, msg: str) -> None:
        p = theme.palette
        self._btn_gui_check.configure(state="normal")
        self._gui_status.configure(
            text=msg, text_color=p.success if success else p.error)
        if not success:
            self._enable_gui_update(True)
        else:
            # Перезапускаем проверку — точка погаснет если всё актуально
            root = self.winfo_toplevel()
            if hasattr(root, "_check_updates_bg"):
                self.after(2000, root._check_updates_bg)

    # ──────────────────────────────────────────────
    #  Core обновление
    # ──────────────────────────────────────────────

    def _check_core(self) -> None:
        self._btn_core_check.configure(state="disabled", text="Проверяю…")
        self._core_status.configure(text="Запрос к GitHub…",
                                    text_color=theme.palette.text_muted)
        self._enable_core_update(False)

        def _worker():
            try:
                release = get_latest_release(CORE_REPO)
                self.after(0, self._apply_core_check, release)
            except RateLimitError as e:
                _msg = str(e)
                self.after(0, lambda m=_msg: (
                    self._btn_core_check.configure(state="normal", text="Проверить"),
                    self._core_status.configure(text=m, text_color=theme.palette.warning)
                ))

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_core_check(self, release) -> None:
        p = theme.palette
        self._btn_core_check.configure(state="normal", text="Проверить")
        # Сохраняем в кэш
        if release:
            try:
                root = self.winfo_toplevel()
                if not hasattr(root, "_update_cache"):
                    root._update_cache = {}
                root._update_cache["core"] = release
            except Exception:
                pass

        if not release:
            installed = get_installed_core_version(self._app_dir / "zapret")
            if not installed:
                # Core не установлен и нет интернета — всё равно даём возможность попробовать
                self._core_status.configure(
                    text="Нет подключения. Проверьте интернет и нажмите «Проверить».",
                    text_color=p.warning)
                self._enable_core_update(True)
            else:
                self._core_status.configure(
                    text="Не удалось получить информацию. Проверьте интернет.",
                    text_color=p.error)
            return

        tag = release.get("tag_name", "?")
        # core_latest_lbl скрыт

        installed = get_installed_core_version(self._app_dir / "zapret")
        if installed and installed == tag:
            self._core_status.configure(
                text=f"✓ Core актуален ({tag})", text_color=p.success)
        else:
            self._core_status.configure(
                text=f"Доступно обновление: {tag}" +
                     (f" (установлено: {installed})" if installed else ""),
                text_color=p.warning)
            self._enable_core_update(True)

    def _update_core(self) -> None:
        if not self._core_update_allowed:
            return
        if self._manager and self._manager.is_running:
            self._core_status.configure(
                text="⚠ Остановите zapret перед обновлением Core",
                text_color=theme.palette.warning)
            return

        self._btn_core_update.configure(state="disabled", text="Обновляю…")
        self._btn_core_check.configure(state="disabled")
        self._core_status.configure(text="Начинаем загрузку…",
                                    text_color=theme.palette.text_muted)

        download_and_install_core(
            zapret_dir=self._app_dir / "zapret",
            repo=CORE_REPO,
            on_progress=lambda msg: self.after(
                0, lambda m=msg: self._core_status.configure(text=m)),
            on_done=lambda ok, msg: self.after(0, self._on_core_done, ok, msg),
        )

    def _on_core_done(self, success: bool, message: str) -> None:
        p = theme.palette
        self._btn_core_check.configure(state="normal")
        self._btn_core_update.configure(text="Обновить")

        if success:
            self._core_status.configure(text=f"✓ {message}", text_color=p.success)
            installed = get_installed_core_version(self._app_dir / "zapret")
            if installed:
                self._core_installed_lbl.configure(text=installed)
            if self._on_core_updated:
                self._on_core_updated()
            # Перезапускаем проверку — точка погаснет если всё актуально
            root = self.winfo_toplevel()
            if hasattr(root, "_check_updates_bg"):
                self.after(2000, root._check_updates_bg)
        else:
            self._core_status.configure(text=f"✗ {message}", text_color=p.error)
            self._enable_core_update(True)


    # ──────────────────────────────────────────────
    #  Блок TG Proxy
    # ──────────────────────────────────────────────

    def _build_tg_proxy_block(self, row: int) -> None:
        p = theme.palette
        t = theme.typography
        m = theme.metrics

        card = ctk.CTkFrame(self, fg_color=p.bg_card, corner_radius=m.corner_radius)
        card.grid(row=row, column=0, sticky="ew", padx=m.padding_lg,
                  pady=(0, m.padding_md))
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="TG WS Proxy",
                     font=(t.family_ui, t.size_md, "bold"),
                     text_color=p.text_primary).grid(
            row=0, column=0, sticky="w", padx=m.padding_md,
            pady=(m.padding_md, 4))

        inst_row = ctk.CTkFrame(card, fg_color="transparent")
        inst_row.grid(row=1, column=0, sticky="w", padx=m.padding_md, pady=(0, 2))
        ctk.CTkLabel(inst_row, text="Версия:",
                     font=(t.family_ui, t.size_sm),
                     text_color=p.text_secondary).pack(side="left", padx=(0, 6))

        tgproxy_dir = self._app_dir / "tgproxy"
        installed = self._get_tgproxy_version(tgproxy_dir)
        self._tgproxy_installed_lbl = ctk.CTkLabel(
            inst_row,
            text=installed if installed else "не установлен",
            font=(t.family_ui, t.size_sm),
            text_color=p.text_primary)
        self._tgproxy_installed_lbl.pack(side="left")

        self._tgproxy_status = ctk.CTkLabel(
            card, text="",
            font=(t.family_ui, t.size_xs), text_color=p.text_muted,
            anchor="w", wraplength=450)
        self._tgproxy_status.grid(row=2, column=0,
                                   padx=m.padding_md, pady=(0, 8), sticky="ew")

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=3, column=0,
                     padx=m.padding_md, pady=(0, m.padding_md), sticky="w")

        self._btn_tgproxy_check = ctk.CTkButton(
            btn_row, text="Проверить",
            fg_color=p.bg_input, hover_color=p.bg_hover,
            text_color=p.text_primary, height=m.button_height,
            corner_radius=m.corner_radius,
            command=self._check_tgproxy,
        )
        self._btn_tgproxy_check.pack(side="left", padx=(0, 8))

        # Кнопка активна сразу если не установлен, иначе ждёт проверки
        tgproxy_exe = tgproxy_dir / TGPROXY_EXE
        initial_state = "normal" if not tgproxy_exe.exists() else "disabled"
        self._btn_tgproxy_update = ctk.CTkButton(
            btn_row, text="Обновить",
            fg_color=p.accent, hover_color=p.accent_dim,
            text_color=p.bg_root, height=m.button_height,
            corner_radius=m.corner_radius,
            state=initial_state,
            command=self._update_tgproxy,
        )
        self._btn_tgproxy_update.pack(side="left")

    def _get_tgproxy_version(self, tgproxy_dir) -> str:
        """Получить версию установленного TgWsProxy из version.txt."""
        ver_file = tgproxy_dir / "version.txt"
        if ver_file.exists():
            try:
                return ver_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        exe = tgproxy_dir / TGPROXY_EXE
        if exe.exists():
            return "установлен"
        return ""

    def _check_tgproxy(self) -> None:
        self._btn_tgproxy_check.configure(state="disabled", text="Проверяю…")
        self._tgproxy_status.configure(text="Запрос к GitHub…",
                                        text_color=theme.palette.text_muted)
        self._btn_tgproxy_update.configure(state="disabled")

        def _worker():
            release = get_latest_release(TGPROXY_REPO)
            self.after(0, self._apply_tgproxy_check, release)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_tgproxy_check(self, release) -> None:
        p = theme.palette
        self._btn_tgproxy_check.configure(state="normal", text="Проверить")
        # Сохраняем в кэш
        if release:
            try:
                root = self.winfo_toplevel()
                if not hasattr(root, "_update_cache"):
                    root._update_cache = {}
                root._update_cache["tg"] = release
            except Exception:
                pass

        if not release:
            self._tgproxy_status.configure(
                text="Не удалось получить информацию. Проверьте интернет.",
                text_color=p.error)
            return

        tag = release.get("tag_name", "?")
        tgproxy_dir = self._app_dir / "tgproxy"
        installed = self._get_tgproxy_version(tgproxy_dir)

        if installed and installed == tag:
            self._tgproxy_status.configure(
                text=f"✓ TG Proxy актуален ({tag})", text_color=p.success)
        else:
            label = "Обновить" if installed else "Установить"
            self._btn_tgproxy_update.configure(state="normal")
            self._tgproxy_status.configure(
                text=f"Доступна версия: {tag}" +
                     (f" (установлена: {installed})" if installed else " (не установлен)"),
                text_color=p.warning)

    def _update_tgproxy(self) -> None:
        self._btn_tgproxy_update.configure(state="disabled", text="Скачиваю…")
        self._btn_tgproxy_check.configure(state="disabled")
        self._tgproxy_status.configure(text="Начинаем загрузку…",
                                        text_color=theme.palette.text_muted)

        def _worker():
            try:
                import urllib.request, json
                release = get_latest_release(TGPROXY_REPO)
                if not release:
                    raise ValueError("Не удалось получить информацию о релизе")

                tag = release.get("tag_name", "?")
                # Ищем TgWsProxy_windows.exe в assets
                asset = None
                for a in release.get("assets", []):
                    if a.get("name", "").lower() == TGPROXY_EXE.lower():
                        asset = a
                        break
                # Fallback — любой exe
                if not asset:
                    for a in release.get("assets", []):
                        if a.get("name", "").lower().endswith(".exe"):
                            asset = a
                            break

                if not asset:
                    raise ValueError(f"Файл {TGPROXY_EXE} не найден в релизе {tag}")

                dl_url = asset["browser_download_url"]
                size_mb = asset.get("size", 0) / 1024 / 1024
                self.after(0, lambda: self._tgproxy_status.configure(
                    text=f"Скачиваем {asset['name']} ({size_mb:.1f} МБ)…"))

                req = urllib.request.Request(dl_url, headers={"User-Agent": "FlowZap/1.0"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()

                tgproxy_dir = self._app_dir / "tgproxy"
                tgproxy_dir.mkdir(parents=True, exist_ok=True)
                exe_path = tgproxy_dir / TGPROXY_EXE
                exe_path.write_bytes(data)
                (tgproxy_dir / "version.txt").write_text(tag, encoding="utf-8")

                self.after(0, self._on_tgproxy_done, True, f"TG Proxy установлен ({tag})", tag)
            except Exception as e:
                self.after(0, self._on_tgproxy_done, False, str(e), "")

        threading.Thread(target=_worker, daemon=True).start()

    def _on_tgproxy_done(self, success: bool, message: str, tag: str) -> None:
        p = theme.palette
        self._btn_tgproxy_check.configure(state="normal")
        self._btn_tgproxy_update.configure(text="Обновить")

        if success:
            self._tgproxy_status.configure(text=f"✓ {message}", text_color=p.success)
            tgproxy_dir = self._app_dir / "tgproxy"
            installed = self._get_tgproxy_version(tgproxy_dir)
            self._tgproxy_installed_lbl.configure(
                text=installed if installed else "установлен")
            # Сбросить оранжевую точку и перепроверить обновления
            try:
                root = self.winfo_toplevel()
                updates_btn = getattr(root, "_nav_buttons", {}).get("updates")
                if updates_btn and hasattr(updates_btn, "hide_dot"):
                    updates_btn.hide_dot()
                if hasattr(root, "_do_check_updates"):
                    root.after(1000, lambda: __import__("threading").Thread(
                        target=root._do_check_updates, daemon=True).start())
            except Exception:
                pass
        else:
            self._tgproxy_status.configure(text=f"✗ {message}", text_color=p.error)
            self._btn_tgproxy_update.configure(state="normal")

    def on_activate(self) -> None:
        """При открытии вкладки — читаем кэш из main_window, не делаем новых запросов."""
        # Обновляем установленные версии
        installed_core = get_installed_core_version(self._app_dir / "zapret")
        self._core_installed_lbl.configure(
            text=installed_core if installed_core else "не установлен")

        tg_ver_file = self._app_dir / "tgproxy" / "version.txt"
        if tg_ver_file.exists():
            try:
                installed_tg = tg_ver_file.read_text(encoding="utf-8").strip()
                self._tg_installed_lbl.configure(text=installed_tg)
            except Exception:
                pass

        # Применяем данные из кэша main_window если есть
        root = self.winfo_toplevel()
        cache = getattr(root, "_update_cache", None)
        if not cache or cache.get("last_checked") is None:
            # Кэш пустой — ничего не показываем, ждём фоновой проверки
            return

        # Применяем кэшированные данные к UI
        gui_release = cache.get("gui_release")
        if gui_release:
            self._apply_gui_check(gui_release)

        core_release = cache.get("core_release")
        if core_release:
            self._apply_core_check(core_release)

        tg_release = cache.get("tg_release")
        if tg_release:
            self._apply_tgproxy_check(tg_release)
