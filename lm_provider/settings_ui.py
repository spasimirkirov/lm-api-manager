"""LM API Manager window (--settings): add, edit, remove, test providers. tkinter, no web view.

One maximized window: a header that says where the keys live, the providers on the left, the
selected provider's form on the right. Keys go straight from the entry field to the credential
store; the window shows only the last four characters of a saved key and never prints one.
"""

import asyncio
import threading
import tkinter as tk
import webbrowser
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
from .presets import CUSTOM_PRESET_ID, PRESETS, preset_for, preset_services
from .ticks import cell_glyph, count_text, toggle_model, toggle_service, visible_models
from .upstream import UpstreamError, list_models, new_session

APP_NAME = "LM API Manager"
BG = "#0d1117"
PANEL = "#161b22"
FG = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#22d3ee"
DIVIDER = "#30363d"
SELECTED = "#0e4a57"
OK = "#3fb950"
SERVICE_LABELS = {"chat": "Chat", "vision": "Vision", "tts": "Voice (TTS)", "images": "Pictures"}
SERVICE_ORDER = tuple(SERVICE_LABELS)
SERVICE_TAGS = {"chat": "chat", "vision": "vision", "tts": "voice", "images": "pictures"}
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
    style.configure("Header.TFrame", background=PANEL)
    style.configure("Brand.TLabel", background=PANEL, foreground=FG, font=("Segoe UI Semibold", 18))
    style.configure("HeaderMuted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("Shield.TLabel", background=PANEL, foreground=OK, font=("Segoe UI Semibold", 10))
    style.configure("Section.TLabel", foreground=MUTED, font=("Segoe UI Semibold", 9))
    style.configure("Empty.TLabel", foreground=FG, font=("Segoe UI Semibold", 14))
    style.configure("Danger.TButton", foreground="#f85149", bordercolor="#f85149")
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
    style.configure("TCheckbutton", background=BG, foreground=FG)
    style.map("TCheckbutton", background=[("active", BG)], foreground=[("disabled", MUTED)])
    style.configure("Tag.TLabel", background=PANEL, foreground=ACCENT, font=("Segoe UI", 9), padding=(6, 1))
    style.configure("Link.TLabel", foreground=ACCENT, font=("Segoe UI", 10, "underline"))


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


def tags_text(services) -> str:
    return "  ".join(SERVICE_TAGS[s] for s in services if s in SERVICE_TAGS)


def link(parent, text: str, url: str):
    """A text link that opens in the default browser."""
    label = ttk.Label(parent, text=text, style="Link.TLabel", cursor="hand2")
    label.bind("<Button-1>", lambda _e: webbrowser.open(url))
    return label


def describe_error(error) -> str:
    if isinstance(error, UpstreamError):
        return error.message
    return f"Test failed: {type(error).__name__}"


class ProviderForm(ttk.Frame):
    """Add or edit one provider, shown in place of the list in the same window.
    `on_save(provider, new_key_or_None)` stores it; `on_close()` goes back to the list."""

    def __init__(self, master, vault, existing: Provider, taken_ids, on_save, on_close, on_remove=None):
        super().__init__(master, padding=(24, 18))
        self.vault = vault
        self.existing = existing
        self.taken_ids = taken_ids
        self.on_save = on_save
        self.on_close = on_close
        self.on_remove = on_remove
        self.confirm_remove = False
        self.saved_key = vault.get(existing.id) if existing else ""

        self.preset_labels = [p.name for p in PRESETS] + [CUSTOM_LABEL]
        preset = preset_for(existing.preset) if existing else PRESETS[0]
        self.preset_var = tk.StringVar(value=preset.name if preset else CUSTOM_LABEL)
        self.name_var = tk.StringVar(value=existing.name if existing else PRESETS[0].name)
        self.url_var = tk.StringVar(value=existing.base_url if existing else PRESETS[0].base_url)
        self.key_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.show_models())
        self.models_note_var = tk.StringVar()
        # Ticked models with their services, every model the provider offered when tested,
        # and the ones it reports as free.
        self.services = {m["id"]: list(m["services"]) for m in existing.models} if existing else {}
        # A provider saved before 0.3.1 with no ticks: its models get ticked for chat on first test.
        self.pending_legacy = bool(existing and existing.legacy_all_chat)
        self.ticked_only_var = tk.BooleanVar(value=False)
        self.ticked_only_var.trace_add("write", lambda *_: self.show_models())
        self.offered = []
        self.free = set()
        self.visible = []

        ttk.Label(self, text=existing.name if existing else "Add provider", style="Title.TLabel").grid(
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
                ttk.Label(left, text=hint, style="Muted.TLabel", wraplength=560).pack(anchor="w")
            ttk.Frame(left, height=8).pack()

        preset_box = ttk.Combobox(left, textvariable=self.preset_var, values=self.preset_labels,
                                  state="readonly")
        preset_box.bind("<<ComboboxSelected>>", self.on_preset)
        field("Provider", preset_box, "Fills the base URL. Pick Custom for any other OpenAI-compatible endpoint.")
        self.catalog = ttk.Frame(left)
        self.catalog.pack(fill="x", pady=(0, 6))
        field("Name", ttk.Entry(left, textvariable=self.name_var))
        self.url_entry = ttk.Entry(left, textvariable=self.url_var)
        field("Base URL", self.url_entry, "The address that ends before /chat/completions.")
        key_hint_text = (f"Saved key {key_hint(self.saved_key)}. Leave empty to keep it."
                         if self.saved_key else "Stored in Windows Credential Manager, never in a file.")
        field("API key", ttk.Entry(left, textvariable=self.key_var, show="\u2022"), key_hint_text)

        ttk.Label(right, text="Models").pack(anchor="w", pady=(0, 2))
        ttk.Label(right, style="Muted.TLabel", wraplength=560,
                  text="Test connection loads the list. Tick what each model is for; a model with at least one "
                       "tick is given to Pal Engine, the rest are not. Click a model name to tick its likely "
                       "use. (free) means the provider reports a zero price."
                  ).pack(anchor="w", pady=(0, 6))
        filter_row = ttk.Frame(right)
        filter_row.pack(fill="x", pady=(0, 4))
        ttk.Label(filter_row, text="Filter", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        ttk.Entry(filter_row, textvariable=self.filter_var).pack(side="left", fill="x", expand=True)
        ttk.Checkbutton(filter_row, text="Show ticked only", variable=self.ticked_only_var).pack(
            side="left", padx=(8, 0))
        ttk.Button(filter_row, text="Clear ticks", command=self.clear_models).pack(side="left", padx=(6, 0))
        list_box = ttk.Frame(right)
        list_box.pack(fill="both", expand=True)
        columns = ("model", *SERVICE_ORDER, "price")
        self.table = ttk.Treeview(list_box, columns=columns, show="headings", selectmode="none")
        self.table.heading("model", text="Model", anchor="w")
        self.table.column("model", width=200, minwidth=120, stretch=True, anchor="w")
        for name in SERVICE_ORDER:
            self.table.heading(name, text=SERVICE_LABELS[name])
            self.table.column(name, width=78, minwidth=64, stretch=False, anchor="center")
        self.table.heading("price", text="Price", anchor="w")
        self.table.column("price", width=60, minwidth=50, stretch=False, anchor="w")
        scrollbar = ttk.Scrollbar(list_box, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="left", fill="y")
        self.table.bind("<Button-1>", self.on_table_click)
        ttk.Label(right, textvariable=self.models_note_var, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        ttk.Separator(self).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 10))
        buttons = ttk.Frame(self)
        buttons.grid(row=4, column=0, columnspan=2, sticky="w")
        self.test_button = ttk.Button(buttons, text="Test connection", command=self.test)
        self.test_button.pack(side="left")
        ttk.Button(buttons, text="Save", style="Accent.TButton", command=self.save).pack(side="left", padx=(8, 0))
        if existing and on_remove:
            # Confirmed in place: the first click asks, the second removes.
            self.remove_button = ttk.Button(buttons, text="Remove", style="Danger.TButton", command=self.remove)
            self.remove_button.pack(side="left", padx=(24, 0))
        else:
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

    def allowed_services(self) -> tuple:
        return preset_services(self.preset_id())

    def row_values(self, name: str) -> tuple:
        allowed = self.allowed_services()
        return (name, *(cell_glyph(self.services, name, svc, allowed) for svc in SERVICE_ORDER),
                "(free)" if name in self.free else "")

    def show_models(self):
        """Fills the table from the offered models plus the ticked ones, through the filter."""
        self.visible = visible_models(self.offered, self.services, self.filter_var.get(),
                                      self.ticked_only_var.get())
        top = self.table.yview()[0]
        self.table.delete(*self.table.get_children())
        for name in self.visible:
            self.table.insert("", "end", iid=name, values=self.row_values(name))
        self.table.yview_moveto(top)
        self.update_models_note()

    def refresh_row(self, name: str):
        """One toggle redraws one row (the whole table only when "ticked only" is on)."""
        if self.ticked_only_var.get():
            self.show_models()
            return
        if self.table.exists(name):
            self.table.item(name, values=self.row_values(name))
        self.update_models_note()

    def on_table_click(self, event):
        if self.table.identify_region(event.x, event.y) != "cell":
            return None
        name = self.table.identify_row(event.y)
        if not name:
            return None
        column = int(self.table.identify_column(event.x).lstrip("#") or 0)
        allowed = self.allowed_services()
        if column == 1:
            toggle_model(self.services, name, allowed)
        elif 2 <= column <= 1 + len(SERVICE_ORDER):
            toggle_service(self.services, name, SERVICE_ORDER[column - 2], allowed)
        else:
            return "break"
        self.pending_legacy = False
        self.refresh_row(name)
        return "break"

    def clear_models(self):
        self.services.clear()
        self.pending_legacy = False
        self.show_models()

    def update_models_note(self):
        self.models_note_var.set(count_text(self.services, len(self.offered)))

    def show_catalog(self):
        """What the selected provider is good for, its services and where to get a key."""
        for child in self.catalog.winfo_children():
            child.destroy()
        preset = preset_for(self.preset_id())
        if not preset:
            ttk.Label(self.catalog, style="Muted.TLabel", wraplength=560,
                      text="Any other OpenAI-compatible endpoint. All services can be set per model.").pack(anchor="w")
            return
        ttk.Label(self.catalog, text=preset.description, style="Muted.TLabel", wraplength=560).pack(anchor="w")
        tags = ttk.Frame(self.catalog)
        tags.pack(anchor="w", pady=(4, 0))
        for service in preset.services:
            ttk.Label(tags, text=SERVICE_TAGS[service], style="Tag.TLabel").pack(side="left", padx=(0, 6))
        links = ttk.Frame(self.catalog)
        links.pack(anchor="w", pady=(4, 0))
        link(links, "Get an API key", preset.key_url).pack(side="left")
        if preset.pricing_url:
            link(links, "Pricing", preset.pricing_url).pack(side="left", padx=(16, 0))

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
        self.show_catalog()
        if not initial:
            self.show_models()

    def build(self) -> Provider:
        provider_id = self.existing.id if self.existing else unique_id(self.name_var.get(), self.taken_ids)
        models = [{"id": name, "services": list(self.services[name])}
                  for name in sorted(self.services, key=str.lower) if self.services[name]]
        return validate_provider(Provider(
            id=provider_id,
            name=self.name_var.get(),
            base_url=self.url_var.get(),
            type="llm",
            preset=self.preset_id(),
            models=models,
            legacy_all_chat=self.pending_legacy and not models,
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
            if self.pending_legacy and not self.services and self.offered:
                # Saved before 0.3.1 with no ticks: all its models as chat, once. Save keeps it.
                self.services = {name: ["chat"] for name in self.offered}
                self.pending_legacy = False
            self.show_models()
            free = f", {len(self.free)} free" if self.free else ""
            self.status_var.set("" if quiet else f"Connected: {len(entries)} models{free}.")

        run_in_thread(self, lambda: fetch_models(provider, key), done)

    def remove(self):
        if not self.confirm_remove:
            self.confirm_remove = True
            self.remove_button.configure(text="Confirm remove")
            self.status_var.set(f"Click Confirm remove to delete {self.existing.name} and its saved API key.")
            return
        self.on_remove(self.existing)

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
        self.on_close(provider.id)


class SettingsWindow:
    def __init__(self, root: tk.Tk, vault):
        self.root = root
        self.vault = vault
        self.providers = load_providers()
        self.form = None
        self.shown = []  # provider ids in list order
        root.title(f"{APP_NAME} {__version__}")
        root.geometry("1100x720")
        root.minsize(900, 600)
        try:
            root.state("zoomed")  # maximized on Windows
        except tk.TclError:
            pass

        header = ttk.Frame(root, style="Header.TFrame", padding=(24, 14))
        header.pack(fill="x")
        brand = ttk.Frame(header, style="Header.TFrame")
        brand.pack(side="left")
        ttk.Label(brand, text=APP_NAME, style="Brand.TLabel").pack(anchor="w")
        ttk.Label(brand, style="HeaderMuted.TLabel",
                  text=f"Version {__version__} · the bridge between Pal Engine and your AI providers").pack(anchor="w")
        shield = ttk.Frame(header, style="Header.TFrame")
        shield.pack(side="right")
        ttk.Label(shield, text="\U0001F512 Your keys stay on this PC", style="Shield.TLabel").pack(anchor="e")
        ttk.Label(shield, style="HeaderMuted.TLabel",
                  text="Encrypted in Windows Credential Manager. Pal Engine never sees them.").pack(anchor="e")
        ttk.Separator(root).pack(fill="x")

        # The footer is packed before the body, so the body can never push it out.
        footer = ttk.Frame(root, padding=(24, 6))
        footer.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar(value="Usage is billed by each provider to your own account, not by Pal Engine.")
        ttk.Label(footer, textvariable=self.status_var, style="Muted.TLabel").pack(anchor="w")
        ttk.Separator(root).pack(side="bottom", fill="x")

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True)
        side = ttk.Frame(body, padding=(18, 16, 12, 16), width=300)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        ttk.Label(side, text="PROVIDERS", style="Section.TLabel").pack(anchor="w")
        ttk.Button(side, text="+  Add provider", style="Accent.TButton", command=self.add).pack(
            side="bottom", anchor="w", pady=(10, 0))
        self.listbox = tk.Listbox(side, activestyle="none", exportselection=False, bg=BG, fg=FG,
                                  selectbackground=SELECTED, selectforeground=ACCENT, highlightthickness=0,
                                  relief="flat", borderwidth=0, font=("Segoe UI", 11))
        self.listbox.pack(fill="both", expand=True, pady=(8, 0))
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self.show_selected())
        ttk.Separator(body, orient="vertical").pack(side="left", fill="y")
        self.content = ttk.Frame(body)
        self.content.pack(side="left", fill="both", expand=True)

        self.root.bind("<Escape>", lambda _e: self.show_selected())
        self.refresh(select_id=self.providers[0].id if self.providers else None)

    def selected_id(self):
        picked = self.listbox.curselection()
        return self.shown[picked[0]] if picked else None

    def refresh(self, select_id=None):
        """The provider list (a muted dot: no saved key), then the selected one's form."""
        self.listbox.delete(0, "end")
        self.shown = []
        for provider in self.providers:
            has_key = bool(self.vault.get(provider.id))
            self.listbox.insert("end", f"  ●  {provider.name}")
            self.listbox.itemconfigure("end", foreground=FG if has_key else MUTED)
            self.shown.append(provider.id)
        if select_id in self.shown:
            index = self.shown.index(select_id)
            self.listbox.selection_set(index)
            self.listbox.see(index)
        self.show_selected()

    def clear_content(self):
        self.form = None
        for child in self.content.winfo_children():
            child.destroy()

    def show_selected(self):
        provider_id = self.selected_id()
        provider = next((p for p in self.providers if p.id == provider_id), None)
        if provider:
            self.open_form(provider)
        else:
            self.show_empty()

    def show_empty(self):
        self.clear_content()
        empty = ttk.Frame(self.content, padding=(32, 28))
        empty.pack(fill="both", expand=True)
        if self.providers:
            ttk.Label(empty, text="Pick a provider on the left", style="Empty.TLabel").pack(anchor="w")
            return
        ttk.Label(empty, text="Connect your first AI provider", style="Empty.TLabel").pack(anchor="w")
        steps = ("1.  Click Add provider and pick a service (OpenAI, DeepSeek, OpenRouter...).",
                 "2.  Paste your API key. It goes straight into Windows Credential Manager.",
                 "3.  Test connection, tick the models Pal Engine may use, and Save.",
                 "4.  In Pal Engine, choose this add-on as the provider.")
        for step in steps:
            ttk.Label(empty, text=step, style="Muted.TLabel").pack(anchor="w", pady=(8, 0))
        ttk.Button(empty, text="+  Add provider", style="Accent.TButton", command=self.add).pack(
            anchor="w", pady=(18, 0))
        ttk.Label(empty, text="PROVIDERS YOU CAN CONNECT", style="Section.TLabel").pack(anchor="w", pady=(22, 4))
        for preset in PRESETS:
            row = ttk.Frame(empty)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=preset.name, width=16).pack(side="left")
            ttk.Label(row, text=tags_text(preset.services), style="Muted.TLabel", width=34).pack(side="left")
            ttk.Label(row, text=preset.description, style="Muted.TLabel").pack(side="left")

    def open_form(self, existing):
        self.clear_content()
        self.form = ProviderForm(self.content, self.vault, existing, {p.id for p in self.providers},
                                 self.store, self.close_form, on_remove=self.remove)
        self.form.pack(fill="both", expand=True)

    def close_form(self, saved_id=None):
        if saved_id:
            self.refresh(select_id=saved_id)
        else:
            self.show_selected()

    def store(self, provider: Provider, new_key):
        # Key first: the server reloads when the file changes and must find the key then.
        if new_key:
            self.vault.set(provider.id, new_key)
        others = [p for p in self.providers if p.id != provider.id]
        index = next((i for i, p in enumerate(self.providers) if p.id == provider.id), len(others))
        others.insert(index, provider)
        save_providers(others)
        self.providers = others
        self.status_var.set(f"Saved {provider.name}.")

    def add(self):
        self.listbox.selection_clear(0, "end")
        self.open_form(None)

    def remove(self, provider: Provider):
        self.providers = [p for p in self.providers if p.id != provider.id]
        save_providers(self.providers)
        self.vault.delete(provider.id)
        self.status_var.set(f"Removed {provider.name} and its saved API key.")
        self.refresh(select_id=self.providers[0].id if self.providers else None)


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
