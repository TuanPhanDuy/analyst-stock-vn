"""Output formatters: rich table + JSON."""
import json

from rich.console import Console
from rich.table import Table

console = Console()


def print_table(ranked: dict, timeframe: str) -> None:
    for action in ("buy", "sell"):
        items = ranked.get(action, [])
        if not items:
            continue
        title = f"Top {action.upper()} — {timeframe.upper()}"
        table = Table(title=title, show_lines=True)
        table.add_column("Ticker", style="bold cyan")
        table.add_column("Price (VND)", justify="right")
        table.add_column("Score", justify="right")
        table.add_column("Recommendation", style="bold")
        table.add_column("Key Reasons")

        for item in items:
            reasons = "; ".join(
                s["reason"] for s in item["signals"] if s["action"] != "HOLD"
            )
            color = "green" if action == "buy" else "red"
            table.add_row(
                item["ticker"],
                f"{item['price']:,.0f}",
                f"{item['composite']:+.3f}",
                f"[{color}]{item['recommendation']}[/{color}]",
                reasons[:80],
            )
        console.print(table)


def print_json(ranked: dict) -> None:
    console.print_json(json.dumps(ranked, ensure_ascii=False, indent=2))
