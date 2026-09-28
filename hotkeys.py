"""
VideoHook v2.0 — Hotkeys definitions & Help dialog
"""

from typing import Dict
import customtkinter as ctk

HOTKEYS: Dict[str, str] = {
    "Space": "Play / Pause видео",
    "S": "Разрезать в текущей позиции плейхеда",
    "Ctrl+K": "Разрезать (альтернативная клавиша)",
    "Ctrl+Z": "Отменить последний разрез (Undo)",
    "Ctrl+Y": "Повторить отменённый разрез (Redo)",
    "Ctrl+A": "Выделить все сегменты для пакетного экспорта",
    "Delete": "Удалить / скрыть выделенный сегмент",
    "Left": "Перемотка на 1 сек назад",
    "Right": "Перемотка на 1 сек вперед",
    "Shift+Left": "Горизонтальный скролл таймлайна влево",
    "Shift+Right": "Горизонтальный скролл таймлайна вправо",
    "+ / =": "Увеличить масштаб таймлайна (Zoom In)",
    "-": "Уменьшить масштаб таймлайна (Zoom Out)",
    "0": "Вписать ролик в ширину окна (Zoom Fit)",
    "Home": "Перейти в начало таймлайна (0:00)",
    "End": "Перейти в конец ролика",
    "M": "Включить / выключить звук (Mute toggle)",
    "1": "Перейти к сегменту 1",
    "2": "Перейти к сегменту 2",
    "3": "Перейти к сегменту 3",
    "4": "Перейти к сегменту 4",
    "5": "Перейти к сегменту 5",
    "6": "Перейти к сегменту 6",
    "7": "Перейти к сегменту 7",
    "8": "Перейти к сегменту 8",
    "9": "Перейти к сегменту 9",
    "Ctrl+S": "Сохранить / экспортировать финальный ролик",
    "?": "Показать эту справку горячих клавиш",
}


def show_hotkeys_dialog(parent):
    """Displays a modal dialog with all keyboard shortcuts."""
    dialog = ctk.CTkToplevel(parent)
    dialog.title("⌨️ Горячие клавиши — VideoHook v2.0")
    dialog.geometry("560x620")
    dialog.minsize(480, 500)
    dialog.transient(parent)
    dialog.grab_set()

    header = ctk.CTkFrame(dialog, fg_color="#18181b", corner_radius=0, height=50)
    header.pack(fill="x", padx=0, pady=0)
    ctk.CTkLabel(
        header, text="⌨️ Горячие клавиши редактора", font=ctk.CTkFont(size=16, weight="bold")
    ).pack(pady=12, padx=15, anchor="w")

    scroll = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
    scroll.pack(fill="both", expand=True, padx=15, pady=10)

    for key, desc in HOTKEYS.items():
        row = ctk.CTkFrame(scroll, fg_color=("gray85", "gray20"), corner_radius=6)
        row.pack(fill="x", pady=3)
        ctk.CTkLabel(
            row, text=key, font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color="#38bdf8", width=120, anchor="w"
        ).pack(side="left", padx=(12, 8), pady=6)
        ctk.CTkLabel(
            row, text=desc, font=ctk.CTkFont(size=12), anchor="w"
        ).pack(side="left", fill="x", expand=True, padx=8, pady=6)

    btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_row.pack(fill="x", padx=15, pady=(4, 12))
    ctk.CTkButton(
        btn_row, text="Закрыть (Esc)", width=120, command=dialog.destroy
    ).pack(side="right")
    dialog.bind("<Escape>", lambda e: dialog.destroy())
