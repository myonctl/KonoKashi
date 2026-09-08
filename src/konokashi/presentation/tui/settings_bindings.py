"""Settings navigation bindings and concise help from the same definitions."""

from textual.binding import Binding

NAVIGATION_BINDINGS = [
    Binding("q", "quit_settings", "Quit"),
    Binding("ctrl+c", "quit_settings", "Quit", show=False),
    Binding("question_mark", "show_help", "Help"),
    Binding("slash", "focus_search", "Search"),
    Binding("escape", "escape", "Back", show=False),
    Binding("r", "reset_selected", "Reset", show=False),
    Binding("j", "next_setting", "Next", show=False),
    Binding("k", "previous_setting", "Previous", show=False),
    Binding("1", "category(0)", "Players", show=False),
    Binding("2", "category(1)", "Lyrics", show=False),
    Binding("3", "category(2)", "Desktop", show=False),
    Binding("4", "category(3)", "Library", show=False),
    Binding("5", "category(4)", "Appearance", show=False),
    Binding("6", "category(5)", "Typography", show=False),
    Binding("7", "category(6)", "Colors", show=False),
    Binding("8", "category(7)", "Layout", show=False),
    Binding("9", "category(8)", "Visibility", show=False),
]


def help_text() -> str:
    """Keep named application actions synchronized with the actual bindings."""
    keys = {binding.action: binding.key for binding in NAVIGATION_BINDINGS}
    return (
        "Move around\n"
        "  ↑/↓ or j/k   Navigate settings or categories\n"
        f"  {keys['category(0)']}-{keys['category(8)']}            Choose a category\n"
        "  Tab / Shift+Tab   Move focus; Enter opens the editor\n\n"
        "Find and edit\n"
        "  /            Search titles, descriptions or canonical keys\n"
        "  Enter        Leave search for the selected result\n"
        "  Enter/Space  Toggle a focused switch or press a button\n"
        "  Numbers, text and lists save with Apply; invalid drafts are retained\n"
        f"  {keys['reset_selected']}            Reset the selected setting "
        "to its default\n\n"
        "Help and exit\n"
        "  Esc          Clear search, cancel a dialog or return to settings\n"
        "  ?            Open / close this help\n"
        "  q / Ctrl+C   Quit (q types normally in text fields)\n\n"
        "Mouse: click categories, rows and controls; scroll lists and details.\n"
        "Changes use the canonical validated TOML service shared with the desktop.\n"
        "An invalid file keeps your last-known-good values active until repaired."
    )
