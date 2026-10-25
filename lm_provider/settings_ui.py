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
        return await list_models(session, provider, key, use_allow_list=False)


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
        self.models_var = tk.StringVar(value=", ".join(existing.models) if existing else "")
        self.type_var = tk.StringVar(value=TYPE_LABELS["llm"])
        self.status_var = tk.StringVar()

        ttk.Label(self, text="Edit provider" if existing else "Add provider", style="Title.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Separator(self).grid(row=1, column=0, sticky="ew", pady=(8, 10))
        row = 2

        def field(label, widget, hint=""):
            nonlocal row
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", pady=(0, 2))
            widget.grid(row=row + 1, column=0, sticky="ew", pady=(0, 2))
            if hint:
                ttk.Label(self, text=hint, style="Muted.TLabel", wraplength=680).grid(row=row + 2, column=0, sticky="w")
            row += 3
            ttk.Frame(self, height=8).grid(row=row, column=0)
            row += 1

        preset_box = ttk.Combobox(self, textvariable=self.preset_var, values=self.preset_labels,
                                  state="readonly")
        preset_box.bind("<<ComboboxSelected>>", self.on_preset)
        field("Provider", preset_box, "Fills the base URL. Pick Custom for any other OpenAI-compatible endpoint.")
        field("Name", ttk.Entry(self, textvariable=self.name_var))
        self.url_entry = ttk.Entry(self, textvariable=self.url_var)
        field("Base URL", self.url_entry, "The address that ends before /chat/completions, e.g. https://api.openai.com/v1")
        key_hint_text = (f"Saved key {key_hint(self.saved_key)}. Leave empty to keep it."
                         if self.saved_key else "Stored in Windows Credential Manager, never in a file.")
        field("API key", ttk.Entry(self, textvariable=self.key_var, show="•"), key_hint_text)
        field("Type", ttk.Combobox(self, textvariable=self.type_var, values=list(TYPE_LABELS.values()),
                                   state="readonly"))
        field("Models (optional)", ttk.Entry(self, textvariable=self.models_var),
              "Comma-separated allow-list. Empty lists every model the provider offers.")

        ttk.Separator(self).grid(row=row, column=0, sticky="ew", pady=(0, 10))
        row += 1
        buttons = ttk.Frame(self)
        buttons.grid(row=row, column=0, sticky="w")
        self.test_button = ttk.Button(buttons, text="Test connection", command=self.test)
        self.test_button.pack(side="left")
        ttk.Button(buttons, text="Save", style="Accent.TButton", command=self.save).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Cancel", command=self.on_close).pack(side="left", padx=(8, 0))
        ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel", wraplength=680).grid(
            row=row + 1, column=0, sticky="w", pady=(8, 0))
        self.columnconfigure(0, weight=1)
        self.on_preset(initial=True)

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
        models = [m.strip() for m in self.models_var.get().split(",") if m.strip()]
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

    def test(self):
        try:
            provider = self.build()
        except ConfigError as error:
            self.status_var.set(str(error))
            return
        key = self.current_key()
        if not key:
            self.status_var.set("Enter the API key first.")
            return
        self.status_var.set("Testing...")
        self.test_button.state(["disabled"])

        def done(ids, error):
            if not self.winfo_exists():
                return
            self.test_button.state(["!disabled"])
            if error:
                self.status_var.set(describe_error(error))
            else:
                sample = ", ".join(ids[:3]) + (", ..." if len(ids) > 3 else "")
                self.status_var.set(f"Connected: {len(ids)} models" + (f" ({sample})" if ids else "."))

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
        root.geometry("780x640")
        root.minsize(640, 560)
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

        def done(ids, error):
            self.status_var.set(describe_error(error) if error else f"{provider.name}: connected, {len(ids)} models.")

        run_in_thread(self.root, lambda: fetch_models(provider, key), done)


def run_settings() -> int:
    root = tk.Tk()
    apply_theme(root)
    SettingsWindow(root, KeyVault())
    root.mainloop()
    return 0
