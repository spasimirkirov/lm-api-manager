"""Provider settings window (--settings): add, edit, remove, test. tkinter, no web view.

Keys go straight from the entry field to the credential store; the window shows only the last
four characters of a saved key and never prints one.
"""

import asyncio
import threading
import tkinter as tk
from tkinter import ttk

from . import __version__
from .config import (
    ConfigError,
    KeyVault,
    Provider,
    key_hint,
    load_providers,
    save_providers,
    unique_id,
    validate_provider,
)
from .presets import CUSTOM_PRESET_ID, PRESETS, preset_for
from .upstream import UpstreamError, list_models, new_session

BG = "#0d1117"
PANEL = "#161b22"
FG = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#22d3ee"
DIVIDER = "#30363d"
TYPE_LABELS = {"llm": "Chat (LLM)"}
CUSTOM_LABEL = "Custom"


def apply_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(bg=BG)
    style.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=DIVIDER,
                    lightcolor=BG, darkcolor=BG, font=("Segoe UI", 10))
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Title.TLabel", font=("Segoe UI Semibold", 13), foreground=ACCENT)
    style.configure("TButton", background=PANEL, foreground=FG, padding=(12, 5), borderwidth=1)
    style.map("TButton", background=[("active", "#1f2937"), ("disabled", BG)],
              foreground=[("disabled", MUTED)], bordercolor=[("focus", ACCENT)])
    style.configure("Accent.TButton", foreground=ACCENT, bordercolor=ACCENT)
    style.configure("TEntry", fieldbackground=PANEL, foreground=FG, insertcolor=FG, padding=4)
    style.configure("TCombobox", fieldbackground=PANEL, foreground=FG, arrowcolor=FG, padding=3)
    style.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)])
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", "#1f2937")
    style.configure("Treeview", background=BG, fieldbackground=BG, foreground=FG, rowheight=26, borderwidth=0)
    style.configure("Treeview.Heading", background=BG, foreground=MUTED, borderwidth=0, relief="flat")
    style.map("Treeview", background=[("selected", "#1f2937")], foreground=[("selected", ACCENT)])
    style.configure("TSeparator", background=DIVIDER)


def run_in_thread(root, work, done):
    """Runs a coroutine factory off the UI thread and hands the result back to it."""
    def target():
        try:
            result, error = asyncio.run(work()), None
        except Exception as exc:  # shown to the user, already free of keys
            result, error = None, exc
        root.after(0, lambda: done(result, error))

    threading.Thread(target=target, daemon=True).start()


async def fetch_models(provider: Provider, key: str) -> list:
    async with new_session() as session:
        return await list_models(session, provider, key)


def describe_error(error) -> str:
    if isinstance(error, UpstreamError):
        return error.message
    return f"Test failed: {type(error).__name__}"


class ProviderForm(ttk.Frame):
    """Add or edit one provider, shown in place of the list in the same window.
    `on_save(provider, new_key_or_None)` stores it; `on_close()` goes back to the list."""

    def __init__(self, master, vault, existing: Provider, taken_ids, on_save, on_close):
        super().__init__(master, padding=18)
        self.vault = vault
        self.existing = existing
        self.taken_ids = taken_ids
        self.on_save = on_save
        self.on_close = on_close
        self.saved_key = vault.get(existing.id) if existing else ""

        self.preset_labels = [p.name for p in PRESETS] + [CUSTOM_LABEL]
        preset = preset_for(existing.preset) if existing else PRESETS[0]
        self.preset_var = tk.StringVar(value=preset.name if preset else CUSTOM_LABEL)
        self.name_var = tk.StringVar(value=existing.name if existing else PRESETS[0].name)
        self.url_var = tk.StringVar(value=existing.base_url if existing else PRESETS[0].base_url)
        self.key_var = tk.StringVar()
        self.type_var = tk.StringVar(value=TYPE_LABELS["llm"])
        self.status_var = tk.StringVar()
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.show_models())
        self.models_note_var = tk.StringVar()
        # Ticked models (the allow-list), every model the provider offered when tested, and
        # the ones it reports as free.
        self.chosen = set(existing.models) if existing else set()
        self.offered = []
        self.free = set()
        self.visible = []

        ttk.Label(self, text="Edit provider" if existing else "Add provider", style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Separator(self).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 10))
        left = ttk.Frame(self)
        left.grid(row=2, column=0, sticky="nsew", padx=(0, 16))
        right = ttk.Frame(self)
        right.grid(row=2, column=1, sticky="nsew")

        def field(label, widget, hint=""):
            ttk.Label(left, text=label).pack(anchor="w", pady=(0, 2))
            widget.pack(fill="x", pady=(0, 2))
            if hint:
                ttk.Label(left, text=hint, style="Muted.TLabel", wraplength=330).pack(anchor="w")
            ttk.Frame(left, height=8).pack()

        preset_box = ttk.Combobox(left, textvariable=self.preset_var, values=self.preset_labels,
                                  state="readonly")
        preset_box.bind("<<ComboboxSelected>>", self.on_preset)
        field("Provider", preset_box, "Fills the base URL. Pick Custom for any other OpenAI-compatible endpoint.")
        field("Name", ttk.Entry(left, textvariable=self.name_var))
        self.url_entry = ttk.Entry(left, textvariable=self.url_var)
        field("Base URL", self.url_entry, "The address that ends before /chat/completions.")
        key_hint_text = (f"Saved key {key_hint(self.saved_key)}. Leave empty to keep it."
                         if self.saved_key else "Stored in Windows Credential Manager, never in a file.")
        field("API key", ttk.Entry(left, textvariable=self.key_var, show="\u2022"), key_hint_text)
        field("Type", ttk.Combobox(left, textvariable=self.type_var, values=list(TYPE_LABELS.values()),
                                   state="readonly"))

        ttk.Label(right, text="Models").pack(anchor="w", pady=(0, 2))
        ttk.Label(right, style="Muted.TLabel", wraplength=380,
                  text="Click models to tick the ones Pal Engine may use. None ticked means all of them. "
                       "Test connection loads the list. (free) is shown when the provider reports a zero price."
                  ).pack(anchor="w", pady=(0, 6))
        filter_row = ttk.Frame(right)
        filter_row.pack(fill="x", pady=(0, 4))
        ttk.Label(filter_row, text="Filter", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        ttk.Entry(filter_row, textvariable=self.filter_var).pack(side="left", fill="x", expand=True)
        ttk.Button(filter_row, text="Clear ticks", command=self.clear_models).pack(side="left", padx=(6, 0))
        list_box = ttk.Frame(right)
        list_box.pack(fill="both", expand=True)
        self.models_list = tk.Listbox(list_box, selectmode="multiple", exportselection=False, activestyle="none",
                                      bg=PANEL, fg=FG, selectbackground="#0e4a57", selectforeground=ACCENT,
                                      highlightthickness=1, highlightbackground=DIVIDER, highlightcolor=ACCENT,
                                      relief="flat", borderwidth=0, font=("Segoe UI", 10))
        scrollbar = ttk.Scrollbar(list_box, orient="vertical", command=self.models_list.yview)
        self.models_list.configure(yscrollcommand=scrollbar.set)
        self.models_list.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="left", fill="y")
        self.models_list.bind("<<ListboxSelect>>", self.on_models_clicked)
        ttk.Label(right, textvariable=self.models_note_var, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        ttk.Separator(self).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 10))
        buttons = ttk.Frame(self)
        buttons.grid(row=4, column=0, columnspan=2, sticky="w")
        self.test_button = ttk.Button(buttons, text="Test connection", command=self.test)
        self.test_button.pack(side="left")
        ttk.Button(buttons, text="Save", style="Accent.TButton", command=self.save).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Cancel", command=self.on_close).pack(side="left", padx=(8, 0))
        ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel", wraplength=740).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.columnconfigure(0, weight=1, uniform="cols")
        self.columnconfigure(1, weight=1, uniform="cols")
        self.rowconfigure(2, weight=1)
        self.on_preset(initial=True)
        self.show_models()
        if existing and self.saved_key:
            self.test(quiet=True)

    def show_models(self):
        """Fills the list from the offered models plus the ticked ones, through the filter."""
        needle = self.filter_var.get().strip().lower()
        names = sorted(set(self.offered) | self.chosen, key=str.lower)
        self.visible = [name for name in names if needle in name.lower()]
        self.models_list.delete(0, "end")
        for index, name in enumerate(self.visible):
            self.models_list.insert("end", f"  {name}" + ("  (free)" if name in self.free else ""))
            if name in self.chosen:
                self.models_list.selection_set(index)
        self.update_models_note()

    def on_models_clicked(self, _event=None):
        picked = set(self.models_list.curselection())
        for index, name in enumerate(self.visible):
            if index in picked:
                self.chosen.add(name)
            else:
                self.chosen.discard(name)
        self.update_models_note()

    def clear_models(self):
        self.chosen.clear()
        self.show_models()

    def update_models_note(self):
        offered = f" of {len(self.offered)}" if self.offered else ""
        self.models_note_var.set(f"{len(self.chosen)}{offered} ticked" if self.chosen
                                 else ("All models" + (f" ({len(self.offered)})" if self.offered else "")))

    def preset_id(self) -> str:
        label = self.preset_var.get()
        return next((p.id for p in PRESETS if p.name == label), CUSTOM_PRESET_ID)

    def on_preset(self, _event=None, initial=False):
        preset = preset_for(self.preset_id())
        if preset and not initial:
            known_names = {p.name for p in PRESETS}
            self.url_var.set(preset.base_url)
            if not self.name_var.get().strip() or self.name_var.get() in known_names:
                self.name_var.set(preset.name)
        if not preset and not initial:
            self.url_var.set("")
        self.url_entry.configure(state="normal")

    def build(self) -> Provider:
        provider_id = self.existing.id if self.existing else unique_id(self.name_var.get(), self.taken_ids)
        models = sorted(self.chosen, key=str.lower)
        return validate_provider(Provider(
            id=provider_id,
            name=self.name_var.get(),
            base_url=self.url_var.get(),
            type="llm",
            preset=self.preset_id(),
            models=models,
        ))

    def current_key(self) -> str:
        return self.key_var.get().strip() or self.saved_key or ""

    def test(self, quiet=False):
        try:
            provider = self.build()
        except ConfigError as error:
            self.status_var.set(str(error))
            return
        key = self.current_key()
        if not key:
            self.status_var.set("Enter the API key first.")
            return
        self.status_var.set("Loading models..." if quiet else "Testing...")
        self.test_button.state(["disabled"])

        def done(entries, error):
            if not self.winfo_exists():
                return
            self.test_button.state(["!disabled"])
            if error:
                self.status_var.set(describe_error(error))
                return
            self.offered = [entry["id"] for entry in entries]
            self.free = {entry["id"] for entry in entries if entry["free"]}
            self.show_models()
            free = f", {len(self.free)} free" if self.free else ""
            self.status_var.set("" if quiet else f"Connected: {len(entries)} models{free}.")

        run_in_thread(self, lambda: fetch_models(provider, key), done)

    def save(self):
        try:
            provider = self.build()
        except ConfigError as error:
            self.status_var.set(str(error))
            return
        new_key = self.key_var.get().strip() or None
        if not new_key and not self.saved_key:
            self.status_var.set("Enter the API key.")
            return
        try:
            self.on_save(provider, new_key)
        except Exception as error:  # credential store failures
            self.status_var.set(f"Could not save: {type(error).__name__}")
            return
        self.key_var.set("")
        self.on_close()


class SettingsWindow:
    def __init__(self, root: tk.Tk, vault):
        self.root = root
        self.vault = vault
        self.providers = load_providers()
        root.title(f"LM OpenAI Provider {__version__}")
        root.geometry("900x660")
        root.minsize(760, 560)
        self.form = None
        self.pending_remove = None
        # One window: the list, or the add/edit form in its place.
        frame = self.list_frame = ttk.Frame(root, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Providers", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, style="Muted.TLabel", wraplength=700,
                  text="Your own API keys for OpenAI-compatible services. Pal Engine lists their models as "
                       "\"<provider>/<model>\". Usage is billed by the provider to your account.").pack(anchor="w", pady=(2, 10))
        ttk.Separator(frame).pack(fill="x")
        # The bottom rows are packed before the list, so a growing list can never push them out.
        self.status_var = tk.StringVar(value="Add a provider to start: pick one, paste your API key, Save.")
        ttk.Label(frame, textvariable=self.status_var, style="Muted.TLabel", wraplength=700).pack(
            side="bottom", anchor="w", pady=(8, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Add provider", style="Accent.TButton", command=self.add).pack(side="left")
        self.edit_button = ttk.Button(buttons, text="Edit", command=self.edit)
        self.edit_button.pack(side="left", padx=(8, 0))
        self.test_button = ttk.Button(buttons, text="Test connection", command=self.test)
        self.test_button.pack(side="left", padx=(8, 0))
        self.remove_button = ttk.Button(buttons, text="Remove", command=self.remove)
        self.remove_button.pack(side="left", padx=(8, 0))
        ttk.Separator(frame).pack(side="bottom", fill="x")
        self.tree = ttk.Treeview(frame, columns=("name", "url", "key"), show="headings", selectmode="browse",
                                 height=6)
        for column, title, width in (("name", "Name", 170), ("url", "Base URL", 380), ("key", "API key", 110)):
            self.tree.heading(column, text=title, anchor="w")
            self.tree.column(column, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, pady=(4, 4))
        self.tree.bind("<Double-1>", lambda _e: self.edit())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.update_buttons())
        self.root.bind("<Escape>", lambda _e: self.close_form())
        self.refresh()

    def selected(self):
        selection = self.tree.selection()
        return next((p for p in self.providers if p.id in selection), None)

    def update_buttons(self):
        selected = self.selected()
        if not selected or selected.id != self.pending_remove:
            self.pending_remove = None
            self.remove_button.configure(text="Remove")
        state = ["!disabled"] if selected else ["disabled"]
        for button in (self.edit_button, self.test_button, self.remove_button):
            button.state(state)

    def refresh(self, select_id=None):
        self.tree.delete(*self.tree.get_children())
        for provider in self.providers:
            self.tree.insert("", "end", iid=provider.id,
                             values=(provider.name, provider.base_url, key_hint(self.vault.get(provider.id) or "")))
        if select_id and self.tree.exists(select_id):
            self.tree.selection_set(select_id)
        self.update_buttons()

    def store(self, provider: Provider, new_key):
        # Key first: the server reloads when the file changes and must find the key then.
        if new_key:
            self.vault.set(provider.id, new_key)
        others = [p for p in self.providers if p.id != provider.id]
        index = next((i for i, p in enumerate(self.providers) if p.id == provider.id), len(others))
        others.insert(index, provider)
        save_providers(others)
        self.providers = others
        self.refresh(select_id=provider.id)
        self.status_var.set(f"Saved {provider.name}.")

    def open_form(self, existing):
        self.close_form()
        self.list_frame.pack_forget()
        self.form = ProviderForm(self.root, self.vault, existing, {p.id for p in self.providers},
                                 self.store, self.close_form)
        self.form.pack(fill="both", expand=True)

    def close_form(self):
        if self.form is None:
            return
        self.form.destroy()
        self.form = None
        self.list_frame.pack(fill="both", expand=True)

    def add(self):
        self.open_form(None)

    def edit(self):
        provider = self.selected()
        if provider:
            self.open_form(provider)

    def remove(self):
        provider = self.selected()
        if not provider:
            return
        # Confirmed in place: the first click asks, a second click on the same row removes.
        if self.pending_remove != provider.id:
            self.pending_remove = provider.id
            self.remove_button.configure(text="Confirm remove")
            self.status_var.set(f"Click Confirm remove to delete {provider.name} and its saved API key.")
            return
        self.pending_remove = None
        self.providers = [p for p in self.providers if p.id != provider.id]
        save_providers(self.providers)
        self.vault.delete(provider.id)
        self.refresh()
        self.status_var.set(f"Removed {provider.name}.")

    def test(self):
        provider = self.selected()
        if not provider:
            return
        key = self.vault.get(provider.id) or ""
        self.status_var.set(f"Testing {provider.name}...")

        def done(entries, error):
            self.status_var.set(describe_error(error) if error else f"{provider.name}: connected, {len(entries)} models.")

        run_in_thread(self.root, lambda: fetch_models(provider, key), done)


# Windows virtual key codes of the edit shortcuts. Tk matches Ctrl+V by the letter, so with a
# Cyrillic (or Greek, ...) keyboard layout the key reads as another letter and nothing pastes;
# the key code is the same on every layout.
EDIT_KEYCODES = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>", 65: "select_all"}
LATIN_EDIT_KEYS = {"v", "c", "x", "a"}


def select_all(widget) -> None:
    try:
        widget.select_range(0, "end")
        widget.icursor("end")
    except tk.TclError:
        pass


def on_control_key(event):
    """Ctrl+key in an entry field: paste/copy/cut/select all by key code when the layout's letter
    isn't the Latin one Tk expects."""
    if not isinstance(event.widget, (tk.Entry, ttk.Entry)):
        return None
    if str(event.keysym).lower() in LATIN_EDIT_KEYS:
        return None  # Tk's own bindings already handle a Latin layout
    action = EDIT_KEYCODES.get(event.keycode)
    if not action:
        return None
    if action == "select_all":
        select_all(event.widget)
    else:
        event.widget.event_generate(action)
    return "break"


def install_edit_shortcuts(root: tk.Tk) -> None:
    """Ctrl+V/C/X/A on any keyboard layout, and a right-click menu, for every entry field."""
    menu = tk.Menu(root, tearoff=0, bg=PANEL, fg=FG, activebackground="#1f2937", activeforeground=ACCENT)

    def on_right_click(event):
        widget = event.widget
        if not isinstance(widget, (tk.Entry, ttk.Entry)):
            return
        widget.focus_set()
        menu.delete(0, "end")
        menu.add_command(label="Cut", command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_command(label="Copy", command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="Paste", command=lambda: widget.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Select all", command=lambda: select_all(widget))
        menu.tk_popup(event.x_root, event.y_root)

    root.bind_all("<Control-KeyPress>", on_control_key, add="+")
    root.bind_all("<Button-3>", on_right_click, add="+")


def run_settings() -> int:
    root = tk.Tk()
    apply_theme(root)
    install_edit_shortcuts(root)
    SettingsWindow(root, KeyVault())
    root.mainloop()
    return 0
